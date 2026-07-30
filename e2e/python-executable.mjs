import path from "node:path";
import process from "node:process";

export function resolvePythonExecutable({
  environment = process.env,
  platform = process.platform,
  repositoryRoot,
}) {
  const override = environment.PANORAMA_E2E_PYTHON?.trim();
  if (override) {
    return override;
  }

  const platformPath = platform === "win32" ? path.win32 : path.posix;
  return platformPath.join(
    repositoryRoot,
    ".venv",
    platform === "win32" ? "Scripts" : "bin",
    platform === "win32" ? "python.exe" : "python",
  );
}
