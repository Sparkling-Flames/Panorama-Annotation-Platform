import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { resolvePythonExecutable } from "./python-executable.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(currentDirectory, "..");

test("PANORAMA_E2E_PYTHON overrides platform defaults", () => {
  assert.equal(
    resolvePythonExecutable({
      environment: { PANORAMA_E2E_PYTHON: "python-from-ci" },
      platform: "linux",
      repositoryRoot: "/repo",
    }),
    "python-from-ci",
  );
});

test("resolves the checked-in virtual environment convention on Windows and POSIX", () => {
  assert.equal(
    resolvePythonExecutable({ environment: {}, platform: "win32", repositoryRoot: "C:\\repo" }),
    "C:\\repo\\.venv\\Scripts\\python.exe",
  );
  assert.equal(
    resolvePythonExecutable({ environment: {}, platform: "linux", repositoryRoot: "/repo" }),
    "/repo/.venv/bin/python",
  );
});

test("browser CI installs locked Python dependencies and selects setup-python", async () => {
  const workflow = await readFile(
    path.join(repositoryRoot, ".github", "workflows", "ci.yml"),
    "utf8",
  );
  const browserJob = workflow.slice(workflow.indexOf("  browser-e2e:"));

  assert.match(browserJob, /actions\/setup-python@/);
  assert.match(browserJob, /python-version: "3\.11"/);
  assert.match(browserJob, /backend\/requirements\/bootstrap\.lock/);
  assert.match(browserJob, /backend\/requirements\/dev\.lock/);
  assert.match(browserJob, /PANORAMA_E2E_PYTHON: python/);
});

test("browser media fixtures enter through trusted manifest registration", async () => {
  const runner = await readFile(path.join(currentDirectory, "run-tests.mjs"), "utf8");

  assert.doesNotMatch(runner, /MediaObjectRegistration\.objects\.create/);
  assert.match(runner, /register_media_manifest/);
  assert.match(runner, /PANORAMA_E2E_COS_CONTROL_FILE/);
  assert.match(runner, /PANORAMA_E2E_FRONTEND_URL/);
  assert.match(runner, /PANORAMA_E2E_SQLITE_PATH/);
  assert.match(runner, /getAvailablePort/);
});
