from __future__ import annotations

import importlib
from typing import Any

import pytest

pytestmark = pytest.mark.django_db

SOURCE_KEY = "incoming/warehouse-001/high.png"
VERSION_ID = "cos-version-high-001"
CONTENT_SHA256 = "a" * 64
CONTENT_LENGTH = 8_388_608
CRC64ECMA = "12345678901234567890"


def manifest_module() -> Any:
    return importlib.import_module("media.manifest")


def media_models() -> Any:
    return importlib.import_module("media.models")


def manifest_payload(**entry_overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "content_length": CONTENT_LENGTH,
        "content_sha256": CONTENT_SHA256,
        "crc64ecma": CRC64ECMA,
        "format": "png",
        "height": 2048,
        "source_key": SOURCE_KEY,
        "version_id": VERSION_ID,
        "width": 4096,
    }
    entry.update(entry_overrides)
    return {"objects": [entry], "schema_version": 1}


class FakeCosClient:
    def __init__(
        self,
        *,
        versioning_status: str = "Enabled",
        header_overrides: dict[str, str] | None = None,
    ) -> None:
        self.versioning_status = versioning_status
        self.header_overrides = header_overrides or {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, str]:
        self.calls.append(("get_bucket_versioning", kwargs))
        return {"Status": self.versioning_status}

    def head_object(self, **kwargs: object) -> dict[str, str]:
        self.calls.append(("head_object", kwargs))
        headers = {
            "Content-Length": str(CONTENT_LENGTH),
            "Content-Type": "image/png",
            "x-cos-hash-crc64ecma": CRC64ECMA,
            "x-cos-meta-content-sha256": CONTENT_SHA256,
            "x-cos-meta-height": "2048",
            "x-cos-meta-width": "4096",
            "x-cos-version-id": VERSION_ID,
        }
        headers.update(self.header_overrides)
        return headers


def test_trusted_manifest_registers_an_exact_cos_version_idempotently() -> None:
    module = manifest_module()
    models = media_models()
    client = FakeCosClient()

    first = module.register_media_manifest(
        payload=manifest_payload(),
        client=client,
        bucket="annotation-1250000000",
    )
    second = module.register_media_manifest(
        payload=manifest_payload(),
        client=client,
        bucket="annotation-1250000000",
    )

    assert first == second
    assert models.MediaObjectRegistration.objects.count() == 1
    registration = first[0]
    assert registration.source_key == SOURCE_KEY
    assert registration.version_id == VERSION_ID
    assert registration.content_sha256 == CONTENT_SHA256
    assert registration.content_length == CONTENT_LENGTH
    assert registration.crc64ecma == CRC64ECMA
    assert client.calls[0] == (
        "get_bucket_versioning",
        {"Bucket": "annotation-1250000000"},
    )
    assert client.calls[1] == (
        "head_object",
        {
            "Bucket": "annotation-1250000000",
            "Key": SOURCE_KEY,
            "VersionId": VERSION_ID,
        },
    )


def test_manifest_registration_requires_enabled_bucket_versioning() -> None:
    module = manifest_module()
    client = FakeCosClient(versioning_status="Suspended")

    with pytest.raises(module.MediaManifestInvalid, match="versioning"):
        module.register_media_manifest(
            payload=manifest_payload(),
            client=client,
            bucket="annotation-1250000000",
        )

    assert media_models().MediaObjectRegistration.objects.count() == 0


@pytest.mark.parametrize(
    "header_overrides",
    [
        {"Content-Length": str(CONTENT_LENGTH + 1)},
        {"x-cos-hash-crc64ecma": "1"},
        {"x-cos-version-id": "different-version"},
    ],
)
def test_manifest_registration_rejects_cos_integrity_mismatches(
    header_overrides: dict[str, str],
) -> None:
    module = manifest_module()
    client = FakeCosClient(header_overrides=header_overrides)

    with pytest.raises(module.MediaManifestConflict):
        module.register_media_manifest(
            payload=manifest_payload(),
            client=client,
            bucket="annotation-1250000000",
        )

    assert media_models().MediaObjectRegistration.objects.count() == 0
