import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

import { resolvePythonExecutable } from "./python-executable.mjs";

const BACKEND_URL = "http://127.0.0.1:8000";
const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(currentDirectory, "..");
const frontendRoot = path.join(repositoryRoot, "frontend");
const managePy = path.join(repositoryRoot, "backend", "manage.py");
const playwrightCli = path.join(repositoryRoot, "node_modules", "@playwright", "test", "cli.js");
const pythonExecutable = resolvePythonExecutable({ repositoryRoot });
const e2eCredentials = {
  administratorPassword: `e2e-${randomBytes(24).toString("base64url")}`,
  workerChangedPassword: `e2e-${randomBytes(24).toString("base64url")}`,
  workerInitialPassword: `e2e-${randomBytes(24).toString("base64url")}`,
  workspaceWorkerPassword: `e2e-${randomBytes(24).toString("base64url")}`,
};
const fixtureCommand = [
  "import os",
  "from uuid import UUID",
  "from django.contrib.auth import get_user_model",
  "from media.manifest import register_media_manifest",
  "from panorama_annotation.test_settings import E2ECosClient, E2E_MEDIA_MANIFEST",
  "User = get_user_model()",
  "User.objects.create_superuser(username='e2e-admin', password=os.environ['PANORAMA_E2E_ADMIN_PASSWORD'])",
  "User.objects.create_user(username='e2e-worker', password=os.environ['PANORAMA_E2E_WORKER_INITIAL_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000001'))",
  "User.objects.create_user(username='e2e-workspace-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000002'))",
  "register_media_manifest(payload=E2E_MEDIA_MANIFEST, client=E2ECosClient(), bucket='e2e-controlled-cos')",
].join("; ");

function run(command, arguments_, { cwd = repositoryRoot, env = process.env } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, arguments_, { cwd, env, stdio: "inherit" });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${command} exited with code ${code ?? "unknown"}`));
    });
  });
}

async function waitForBackend(server) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      throw new Error(`Django test server exited with code ${server.exitCode}`);
    }
    try {
      const response = await globalThis.fetch(`${BACKEND_URL}/api/health`);
      if (response.ok) {
        return;
      }
    } catch {
      // The server is still starting.
    }
    await delay(100);
  }
  throw new Error("Timed out waiting for the Django test server");
}

async function stopDjangoServer(server) {
  if (server === undefined || server.exitCode !== null) {
    return;
  }

  const exited = once(server, "exit");
  server.kill("SIGTERM");
  await exited;
}

const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), "panorama-e2e-"));
const testDatabase = path.join(temporaryDirectory, "e2e.sqlite3");
const djangoEnvironment = {
  ...process.env,
  DJANGO_CSRF_TRUSTED_ORIGINS: "http://127.0.0.1:4173",
  DJANGO_SETTINGS_MODULE: "panorama_annotation.test_settings",
  PANORAMA_E2E_ADMIN_PASSWORD: e2eCredentials.administratorPassword,
  PANORAMA_E2E_CONTROLLED_COS: "1",
  PANORAMA_E2E_WORKER_INITIAL_PASSWORD: e2eCredentials.workerInitialPassword,
  PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD: e2eCredentials.workspaceWorkerPassword,
  PANORAMA_TEST_SQLITE_PATH: testDatabase,
};
let djangoServer;
let frontendServer;

try {
  await run(pythonExecutable, [managePy, "migrate", "--noinput"], {
    env: djangoEnvironment,
  });
  await run(pythonExecutable, [managePy, "shell", "-c", fixtureCommand], {
    env: djangoEnvironment,
  });

  djangoServer = spawn(pythonExecutable, [managePy, "runserver", "127.0.0.1:8000", "--noreload"], {
    cwd: repositoryRoot,
    env: djangoEnvironment,
    stdio: "inherit",
  });
  await waitForBackend(djangoServer);

  frontendServer = await createServer({
    root: frontendRoot,
    server: {
      host: "127.0.0.1",
      port: 4173,
      strictPort: true,
    },
  });
  await frontendServer.listen();

  const exitCode = await new Promise((resolve, reject) => {
    const playwright = spawn(process.execPath, [playwrightCli, "test"], {
      cwd: currentDirectory,
      env: {
        ...process.env,
        PANORAMA_E2E_ADMIN_PASSWORD: e2eCredentials.administratorPassword,
        PANORAMA_E2E_BACKEND_URL: BACKEND_URL,
        PANORAMA_E2E_WORKER_CHANGED_PASSWORD: e2eCredentials.workerChangedPassword,
        PANORAMA_E2E_WORKER_INITIAL_PASSWORD: e2eCredentials.workerInitialPassword,
        PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD: e2eCredentials.workspaceWorkerPassword,
      },
      stdio: "inherit",
    });

    playwright.once("error", reject);
    playwright.once("exit", (code) => resolve(code ?? 1));
  });

  process.exitCode = exitCode;
} finally {
  await frontendServer?.close();
  await stopDjangoServer(djangoServer);
  await rm(temporaryDirectory, { force: true, recursive: true });
}
