from __future__ import annotations

import argparse
import json
import re
import shutil

# Only a fixed git ls-files command is executed; no caller input reaches the command.
import subprocess  # nosec B404
import sys
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
EXECUTABLE_ROOTS = {
    ".github",
    "backend",
    "deploy",
    "deployment",
    "e2e",
    "frontend",
    "infra",
    "scripts",
}
ROOT_EXECUTION_FILES = {
    "Dockerfile",
    "Makefile",
    "compose.yml",
    "docker-compose.yml",
    "eslint.config.mjs",
    "package-lock.json",
    "pyproject.toml",
}
EXECUTABLE_SUFFIXES = {
    ".cjs",
    ".js",
    ".json",
    ".lock",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
OLD_RUNTIME_PATTERN = re.compile(
    r"\b(?:hohonet|label(?:[-_\s]+)studio|export_label|active_logs)\b",
    flags=re.IGNORECASE,
)


def repository_files() -> list[Path]:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git executable is required for the default repository scan")

    result = subprocess.run(  # nosec B603
        [git_executable, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )
    return [
        PROJECT_ROOT / raw_path.decode("utf-8")
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    ]


def expanded_files(targets: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        resolved = target if target.is_absolute() else PROJECT_ROOT / target
        if resolved.is_dir():
            files.extend(path for path in resolved.rglob("*") if path.is_file())
        else:
            files.append(resolved)
    return files


def is_execution_surface(path: Path) -> bool:
    if path.resolve() == SELF:
        return False
    try:
        relative_path = path.relative_to(PROJECT_ROOT)
    except ValueError:
        return True
    if not relative_path.parts:
        return False
    if path.is_symlink():
        return True
    if relative_path.name == ".gitmodules":
        return True
    if len(relative_path.parts) == 1 and relative_path.name in ROOT_EXECUTION_FILES:
        return True
    return (
        relative_path.parts[0] in EXECUTABLE_ROOTS
        and relative_path.suffix.lower() in EXECUTABLE_SUFFIXES
    )


def findings_for(path: Path) -> list[int]:
    try:
        content = str(path.readlink()) if path.is_symlink() else path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    return [
        line_number
        for line_number, line in enumerate(content.splitlines(), start=1)
        if OLD_RUNTIME_PATTERN.search(line)
    ]


def package_manifest_findings() -> list[str]:
    manifest_path = PROJECT_ROOT / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checked_values = [
        *manifest.get("scripts", {}).values(),
        *manifest.get("dependencies", {}).keys(),
        *manifest.get("devDependencies", {}).keys(),
        *manifest.get("optionalDependencies", {}).keys(),
    ]
    return [str(value) for value in checked_values if OLD_RUNTIME_PATTERN.search(str(value))]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject executable references to the isolated legacy experiment runtime."
    )
    parser.add_argument("paths", nargs="*", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    explicit_targets = bool(args.paths)
    files = expanded_files(args.paths) if explicit_targets else repository_files()
    scanned_files = [
        path
        for path in sorted(set(files))
        if path.resolve() != SELF and (explicit_targets or is_execution_surface(path))
    ]
    violation_count = 0

    for path in scanned_files:
        for line_number in findings_for(path):
            violation_count += 1
            try:
                display_path = path.relative_to(PROJECT_ROOT)
            except ValueError:
                display_path = path
            print(
                f"{display_path}:{line_number}: old-experiment-runtime: "
                "isolated runtime reference rejected"
            )

    if not explicit_targets:
        for value in package_manifest_findings():
            violation_count += 1
            print(
                "package.json: old-experiment-runtime: "
                f"isolated runtime script or dependency rejected ({value})"
            )

    if violation_count:
        print(f"Repository isolation scan failed with {violation_count} finding(s).")
        return 1

    print(f"Repository isolation scan passed for {len(scanned_files)} execution file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
