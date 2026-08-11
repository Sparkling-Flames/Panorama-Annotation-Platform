import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
PRODUCTION_ENV = {
    "COS_BUCKET": "private-bucket",
    "COS_REGION": "ap-guangzhou",
    "COS_SECRET_ID": "secret-id",
    "COS_SECRET_KEY": "secret-key",
    "DJANGO_ALLOWED_HOSTS": "annotations.example.test",
    "DJANGO_SECRET_KEY": "deployment-secret-key",
    "POSTGRES_PASSWORD": "database-secret",
}


def _import_settings(module: str, **environment: str) -> subprocess.CompletedProcess[str]:
    process_environment = os.environ.copy()
    for name in (
        "COS_BUCKET",
        "COS_REGION",
        "COS_SECRET_ID",
        "COS_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "DJANGO_DEBUG",
        "DJANGO_SECRET_KEY",
        "DJANGO_SETTINGS_MODULE",
        "POSTGRES_PASSWORD",
    ):
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
    result = _import_settings("settings")

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_development_and_test_settings_use_stable_non_production_keys() -> None:
    development_results = [_import_settings("settings", DJANGO_DEBUG="true") for _ in range(2)]
    test_result = _import_settings("test_settings")

    assert [result.returncode for result in development_results] == [0, 0]
    assert {result.stdout.strip() for result in development_results} == {
        "django-insecure-panorama-local-development-only"
    }
    assert test_result.returncode == 0
    assert test_result.stdout.strip() == "django-insecure-panorama-test-only"


@pytest.mark.parametrize("ttl", ["0", "301"])
def test_signed_media_ttl_must_remain_short_and_positive(ttl: str) -> None:
    result = _import_settings(
        "settings",
        COS_SIGNED_URL_SECONDS=ttl,
        DJANGO_DEBUG="true",
    )

    assert result.returncode != 0
    assert "COS_SIGNED_URL_SECONDS" in result.stderr


@pytest.mark.parametrize(
    "missing",
    (
        "COS_BUCKET",
        "COS_REGION",
        "COS_SECRET_ID",
        "COS_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "POSTGRES_PASSWORD",
    ),
)
def test_production_settings_require_external_deployment_secrets(missing: str) -> None:
    environment = PRODUCTION_ENV.copy()
    environment.pop(missing)

    result = _import_settings("settings", **environment)

    assert result.returncode != 0
    assert missing in result.stderr


def test_production_settings_load_when_external_configuration_is_complete() -> None:
    result = _import_settings("settings", **PRODUCTION_ENV)

    assert result.returncode == 0
    assert result.stdout.strip() == PRODUCTION_ENV["DJANGO_SECRET_KEY"]
