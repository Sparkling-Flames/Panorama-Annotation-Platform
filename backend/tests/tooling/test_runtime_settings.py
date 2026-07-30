import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _import_secret_key(module: str, **environment: str) -> subprocess.CompletedProcess[str]:
    process_environment = os.environ.copy()
    for name in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY", "DJANGO_SETTINGS_MODULE"):
        process_environment.pop(name, None)
    process_environment.update(environment)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from panorama_annotation import {module}; print({module}.SECRET_KEY)",
        ],
        capture_output=True,
        check=False,
        cwd=BACKEND_DIR,
        env=process_environment,
        text=True,
    )


def test_production_settings_require_an_explicit_secret_key() -> None:
    result = _import_secret_key("settings")

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_development_and_test_settings_use_stable_non_production_keys() -> None:
    development_results = [_import_secret_key("settings", DJANGO_DEBUG="true") for _ in range(2)]
    test_result = _import_secret_key("test_settings")

    assert [result.returncode for result in development_results] == [0, 0]
    assert {result.stdout.strip() for result in development_results} == {
        "django-insecure-panorama-local-development-only"
    }
    assert test_result.returncode == 0
    assert test_result.stdout.strip() == "django-insecure-panorama-test-only"
