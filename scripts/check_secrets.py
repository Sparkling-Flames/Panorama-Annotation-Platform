from __future__ import annotations

import argparse
import re
import shutil

# Only a fixed git ls-files command is executed; no caller input reaches the command.
import subprocess  # nosec B404
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUDED_PREFIXES = (Path("backend/tests/fixtures/security"),)


@dataclass(frozen=True)
class SecretRule:
    name: str
    pattern: re.Pattern[str]


RULES = (
    SecretRule(
        "credential-assignment",
        re.compile(
            r"""
            \b(?:COS_SECRET_ID|COS_SECRET_KEY|SECRET_KEY|API_KEY|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD)
            \b\s*[:=]\s*(?:["'][^"'\n]{12,}["']|[A-Za-z0-9+/_=\-]{12,}\s*$)
            """,
            flags=re.IGNORECASE | re.VERBOSE,
        ),
    ),
    SecretRule(
        "bearer-token",
        re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    ),
    SecretRule(
        "cos-signed-query",
        re.compile(r"(?i)[?&](?:q-signature|q-key-time|x-cos-security-token)=[^&\s\"']{8,}"),
    ),
    SecretRule(
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)


def repository_files() -> list[Path]:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git executable is required for the default repository scan")

    # The executable is resolved above, arguments are fixed, and shell remains disabled.
    result = subprocess.run(  # nosec B603
        [
            git_executable,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )
    relative_paths = (
        Path(raw_path.decode("utf-8")) for raw_path in result.stdout.split(b"\0") if raw_path
    )
    return [
        PROJECT_ROOT / relative_path
        for relative_path in relative_paths
        if not any(
            relative_path == prefix or prefix in relative_path.parents
            for prefix in DEFAULT_EXCLUDED_PREFIXES
        )
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


def findings_for(path: Path) -> list[tuple[int, str]]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        for rule in RULES:
            if rule.pattern.search(line):
                findings.append((line_number, rule.name))
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="拒绝仓库中的明文凭据、认证 token 和 COS 签名参数。"
    )
    parser.add_argument("paths", nargs="*", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = expanded_files(args.paths) if args.paths else repository_files()
    violation_count = 0

    for path in sorted(set(files)):
        for line_number, rule_name in findings_for(path):
            violation_count += 1
            try:
                display_path = path.relative_to(PROJECT_ROOT)
            except ValueError:
                display_path = path
            print(f"{display_path}:{line_number}: {rule_name}: secret-like content rejected")

    if violation_count:
        print(f"Secret scan failed with {violation_count} finding(s).")
        return 1

    print(f"Secret scan passed for {len(set(files))} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
