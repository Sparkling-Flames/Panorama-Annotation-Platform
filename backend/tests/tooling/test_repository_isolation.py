import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCANNER = PROJECT_ROOT / "scripts" / "check_repository_isolation.py"


def run_scanner(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), *(str(path) for path in paths)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_task_13_8_rejects_old_experiment_runtime_references(tmp_path: Path) -> None:
    old_workspace = "HOHO" + "NET"
    old_database = "label_" + "studio"
    forbidden_source = tmp_path / "runtime.py"
    forbidden_source.write_text(
        f'OLD_WORKSPACE = r"D:\\Work\\{old_workspace}"\n'
        f'OLD_DATABASE_URL = "postgres:///{old_database}"\n',
        encoding="utf-8",
    )

    result = run_scanner(forbidden_source)

    assert result.returncode == 1
    assert "old-experiment-runtime" in result.stdout


def test_task_13_8_default_repository_execution_surface_is_isolated() -> None:
    result = run_scanner()

    assert result.returncode == 0, result.stdout + result.stderr


def test_task_13_8_ci_runs_repository_isolation_scan() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python scripts/check_repository_isolation.py" in workflow
