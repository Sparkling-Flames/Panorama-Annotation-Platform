from __future__ import annotations

import importlib
import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def test_register_media_manifest_command_registers_a_generated_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    command_module = importlib.import_module("media.management.commands.register_media_manifest")
    manifest_path = tmp_path / "trusted-media-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "objects": [
                    {
                        "content_length": 8_388_608,
                        "content_sha256": "a" * 64,
                        "crc64ecma": "12345678901234567890",
                        "format": "png",
                        "height": 2048,
                        "source_key": "incoming/warehouse-001/high.png",
                        "version_id": "cos-version-high-001",
                        "width": 4096,
                    }
                ],
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )

    class FakeCosClient:
        def get_bucket_versioning(self, **_kwargs: object) -> dict[str, str]:
            return {"Status": "Enabled"}

        def head_object(self, **_kwargs: object) -> dict[str, str]:
            return {
                "Content-Length": "8388608",
                "Content-Type": "image/png",
                "x-cos-hash-crc64ecma": "12345678901234567890",
                "x-cos-version-id": "cos-version-high-001",
            }

    monkeypatch.setattr(
        command_module,
        "configured_cos_client",
        lambda: (FakeCosClient(), "annotation-1250000000"),
    )
    output = StringIO()

    call_command("register_media_manifest", manifest_path, stdout=output)

    models: Any = importlib.import_module("media.models")
    registration = models.MediaObjectRegistration.objects.get()
    assert registration.version_id == "cos-version-high-001"
    assert output.getvalue().strip() == "Registered 1 immutable COS object version(s)."
