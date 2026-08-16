import { spawn } from "node:child_process";
import { Buffer } from "node:buffer";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createHttpServer } from "node:http";
import { createServer as createNetServer } from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath, URL } from "node:url";

import { createServer } from "vite";

import { resolvePythonExecutable } from "./python-executable.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(currentDirectory, "..");
const frontendRoot = path.join(repositoryRoot, "frontend");
const managePy = path.join(repositoryRoot, "backend", "manage.py");
const playwrightCli = path.join(repositoryRoot, "node_modules", "@playwright", "test", "cli.js");
const pythonExecutable = resolvePythonExecutable({ repositoryRoot });

async function getAvailablePort() {
  const server = createNetServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("Failed to allocate an E2E TCP port");
  }
  await new Promise((resolve, reject) => {
    server.close((error) => (error === undefined ? resolve() : reject(error)));
  });
  return address.port;
}

const backendPort = await getAvailablePort();
let frontendPort = await getAvailablePort();
while (frontendPort === backendPort) {
  frontendPort = await getAvailablePort();
}
let cosPort = await getAvailablePort();
while (cosPort === backendPort || cosPort === frontendPort) {
  cosPort = await getAvailablePort();
}
const BACKEND_URL = `http://127.0.0.1:${backendPort}`;
const FRONTEND_URL = `http://127.0.0.1:${frontendPort}`;
const COS_URL = `http://127.0.0.1:${cosPort}`;
const HIGH_RESOLUTION_BYTES = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAACAAAAAQCAIAAAD4YuoOAAAANElEQVR4nO3PMREAIAwEwYXBAFUM4F8jEr5Klyuu3wVc1fStueMV6PoIYiOIH0FsBPHtgg/rgQ2SFRRBtwAAAABJRU5ErkJggg==",
  "base64",
);
const COMPRESSED_BYTES = Buffer.from(
  "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/wAALCAAIABABAREA/8QAFQABAQAAAAAAAAAAAAAAAAAABgf/xAAjEAABAgUDBQAAAAAAAAAAAAACAREFIgQAAwcSIQYWIzNR/9oACAEBAAA/AAmm0C9Uny7X1ZG+wNOKyK4JYlmajoJX85orFyJJKKEbEjLs2vyl/wD/2Q==",
  "base64",
);
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
  "User.objects.create_user(username='e2e-tab-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000003'))",
  "User.objects.create_user(username='e2e-takeover-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000004'))",
  "User.objects.create_user(username='e2e-network-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000005'))",
  "User.objects.create_user(username='e2e-media-role-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000006'))",
  "User.objects.create_user(username='e2e-assignment-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000007'))",
  "User.objects.create_user(username='e2e-media-delivery-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000008'))",
  "User.objects.create_user(username='e2e-revision-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000009'))",
  "User.objects.create_user(username='e2e-offline-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000010'))",
  "User.objects.create_user(username='e2e-review-worker', password=os.environ['PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD'], role=User.Role.WORKER, must_change_password=False, worker_id=UUID('00000000-0000-4000-8000-000000000011'))",
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
const cosControlFile = path.join(temporaryDirectory, "cos-control.json");
await writeFile(cosControlFile, JSON.stringify({ drift_source_key: null }), "utf8");
const djangoEnvironment = {
  ...process.env,
  DJANGO_CSRF_TRUSTED_ORIGINS: FRONTEND_URL,
  DJANGO_SETTINGS_MODULE: "panorama_annotation.test_settings",
  PANORAMA_E2E_ADMIN_PASSWORD: e2eCredentials.administratorPassword,
  PANORAMA_E2E_CONTROLLED_COS: "1",
  PANORAMA_E2E_COS_CONTROL_FILE: cosControlFile,
  PANORAMA_E2E_COS_ORIGIN: COS_URL,
  PANORAMA_E2E_WORKER_INITIAL_PASSWORD: e2eCredentials.workerInitialPassword,
  PANORAMA_E2E_WORKSPACE_WORKER_PASSWORD: e2eCredentials.workspaceWorkerPassword,
  PANORAMA_TEST_SQLITE_PATH: testDatabase,
};
let djangoServer;
let frontendServer;
const cosServer = createHttpServer((request, response) => {
  const url = new URL(request.url ?? "/", COS_URL);
  if (!url.searchParams.has("versionId")) {
    response.writeHead(403).end();
    return;
  }
  const highResolution = url.pathname.endsWith("/high.png");
  response.writeHead(200, {
    "Access-Control-Allow-Origin": FRONTEND_URL,
    "Cache-Control": "no-store",
    "Content-Type": highResolution ? "image/png" : "image/jpeg",
  });
  response.end(highResolution ? HIGH_RESOLUTION_BYTES : COMPRESSED_BYTES);
});

try {
  await new Promise((resolve, reject) => {
    cosServer.once("error", reject);
    cosServer.listen(cosPort, "127.0.0.1", resolve);
  });
  await run(pythonExecutable, [managePy, "migrate", "--noinput"], {
    env: djangoEnvironment,
  });
  await run(pythonExecutable, [managePy, "shell", "-c", fixtureCommand], {
    env: djangoEnvironment,
  });

  djangoServer = spawn(
    pythonExecutable,
    [managePy, "runserver", `127.0.0.1:${backendPort}`, "--noreload"],
    {
      cwd: repositoryRoot,
      env: djangoEnvironment,
      stdio: "inherit",
    },
  );
  await waitForBackend(djangoServer);

  frontendServer = await createServer({
    root: frontendRoot,
    server: {
      host: "127.0.0.1",
      port: frontendPort,
      proxy: { "/api": BACKEND_URL },
      strictPort: true,
    },
  });
  await frontendServer.listen();

  const exitCode = await new Promise((resolve, reject) => {
    const playwright = spawn(process.execPath, [playwrightCli, "test", ...process.argv.slice(2)], {
      cwd: currentDirectory,
      env: {
        ...process.env,
        PANORAMA_E2E_ADMIN_PASSWORD: e2eCredentials.administratorPassword,
        PANORAMA_E2E_BACKEND_URL: BACKEND_URL,
        PANORAMA_E2E_COS_CONTROL_FILE: cosControlFile,
        PANORAMA_E2E_COS_ORIGIN: COS_URL,
        PANORAMA_E2E_FRONTEND_URL: FRONTEND_URL,
        PANORAMA_E2E_PYTHON: pythonExecutable,
        PANORAMA_E2E_REPOSITORY_ROOT: repositoryRoot,
        PANORAMA_E2E_SQLITE_PATH: testDatabase,
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
  await new Promise((resolve) => cosServer.close(resolve));
  await rm(temporaryDirectory, { force: true, recursive: true });
}
