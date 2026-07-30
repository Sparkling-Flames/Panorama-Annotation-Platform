import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = PROJECT_ROOT / "scripts" / "validate_openspec_traceability.py"
MANIFEST = PROJECT_ROOT / "tests" / "traceability" / "openspec_scenarios.json"
MISSING_SCENARIO_FIXTURE = (
    PROJECT_ROOT / "backend" / "tests" / "fixtures" / "traceability" / "missing_scenario.json"
)


def run_validator(manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--manifest", str(manifest)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_missing_scenario_mapping_fails_validation():
    result = run_validator(MISSING_SCENARIO_FIXTURE)

    assert result.returncode == 1
    assert "missing capability" in result.stdout


def test_current_change_has_complete_scenario_mapping():
    result = run_validator(MANIFEST)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("test_files", "expected_error"),
    [
        (["backend/tests/test_does_not_exist.py"], "does not exist"),
        (["backend/tests/test_health.py"], "does not contain test ID"),
        (["backend/media/models.py"], "is not a collected test path"),
        (["../outside.py"], "escapes project root"),
    ],
)
def test_implemented_coverage_requires_real_collected_test_evidence(
    tmp_path: Path,
    test_files: list[str],
    expected_error: str,
) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    implemented = next(
        scenario
        for capability in manifest["capabilities"]
        for requirement in capability["requirements"]
        for scenario in requirement["scenarios"]
        if scenario["coverage_status"] == "implemented"
    )
    implemented["test_files"] = test_files
    invalid_manifest = tmp_path / "invalid-traceability.json"
    invalid_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = run_validator(invalid_manifest)

    assert result.returncode == 1
    assert expected_error in result.stdout
