import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCANNER = PROJECT_ROOT / "scripts" / "check_secrets.py"
FORBIDDEN_FIXTURE = (
    PROJECT_ROOT / "backend" / "tests" / "fixtures" / "security" / "forbidden_credentials.txt"
)


def run_scanner(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), *(str(path) for path in paths)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_deliberately_forbidden_fixture_is_rejected():
    result = run_scanner(FORBIDDEN_FIXTURE)

    assert result.returncode == 1
    assert "credential-assignment" in result.stdout
    assert "bearer-token" in result.stdout
    assert "cos-signed-query" in result.stdout


def test_default_repository_scan_excludes_only_the_deliberate_fixture():
    result = run_scanner()

    assert result.returncode == 0, result.stdout + result.stderr
