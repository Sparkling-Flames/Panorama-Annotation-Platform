import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from media.manifest import (
    MediaManifestConflict,
    MediaManifestInvalid,
    register_media_manifest,
)
from media.models import MediaObjectRegistration

pytestmark = pytest.mark.django_db

SOURCE_KEY = "incoming/warehouse-001/high.png"
VERSION_ID = "cos-version-high-001"
CONTENT_SHA256 = "a" * 64
CONTENT_LENGTH = 8_388_608
CRC64ECMA = "12345678901234567890"


def manifest_entry(**entry_overrides: object) -> dict[str, object]:
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
    return entry


def manifest_payload(**entry_overrides: object) -> dict[str, object]:
    return {"objects": [manifest_entry(**entry_overrides)], "schema_version": 1}


class FakeCosClient:
    def __init__(
        self,
        *,
        versioning_status: str = "Enabled",
        header_overrides: dict[str, str] | None = None,
    ) -> None:
        self.versioning_status = versioning_status
        self.header_overrides = header_overrides or {}
        self.in_atomic_block: list[bool] = []
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, str]:
        self.in_atomic_block.append(connection.in_atomic_block)
        self.calls.append(("get_bucket_versioning", kwargs))
        return {"Status": self.versioning_status}

    def head_object(self, **kwargs: object) -> dict[str, str]:
        self.in_atomic_block.append(connection.in_atomic_block)
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


class EntryAwareFakeCosClient(FakeCosClient):
    def __init__(self, entries: tuple[dict[str, object], ...]) -> None:
        super().__init__()
        self.entries = {str(entry["source_key"]): entry for entry in entries}

    def head_object(self, **kwargs: object) -> dict[str, str]:
        self.in_atomic_block.append(connection.in_atomic_block)
        self.calls.append(("head_object", kwargs))
        entry = self.entries[str(kwargs["Key"])]
        return {
            "Content-Length": str(entry["content_length"]),
            "Content-Type": ("image/png" if entry["format"] == "png" else "image/jpeg"),
            "x-cos-hash-crc64ecma": str(entry["crc64ecma"]),
            "x-cos-meta-content-sha256": str(entry["content_sha256"]),
            "x-cos-meta-height": str(entry["height"]),
            "x-cos-meta-width": str(entry["width"]),
            "x-cos-version-id": str(entry["version_id"]),
        }


def test_trusted_manifest_registers_an_exact_cos_version_idempotently() -> None:
    client = FakeCosClient()

    first = register_media_manifest(
        payload=manifest_payload(),
        client=client,
        bucket="annotation-1250000000",
    )
    second = register_media_manifest(
        payload=manifest_payload(),
        client=client,
        bucket="annotation-1250000000",
    )

    assert first == second
    assert MediaObjectRegistration.objects.count() == 1
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
    client = FakeCosClient(versioning_status="Suspended")

    with pytest.raises(MediaManifestInvalid, match="versioning"):
        register_media_manifest(
            payload=manifest_payload(),
            client=client,
            bucket="annotation-1250000000",
        )

    assert MediaObjectRegistration.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_manifest_verifies_cos_before_opening_the_database_transaction() -> None:
    client = FakeCosClient()

    register_media_manifest(
        payload=manifest_payload(),
        client=client,
        bucket="annotation-1250000000",
    )

    assert client.in_atomic_block == [False, False]


def test_concurrent_identical_registration_recovers_from_model_validation_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeCosClient()
    expected = register_media_manifest(
        payload=manifest_payload(),
        client=client,
        bucket="annotation-1250000000",
    )

    def lose_create_race(**_kwargs: object) -> None:
        raise ValidationError("source key already exists")

    monkeypatch.setattr(MediaObjectRegistration.objects, "get_or_create", lose_create_race)

    assert (
        register_media_manifest(
            payload=manifest_payload(),
            client=client,
            bucket="annotation-1250000000",
        )
        == expected
    )


def test_manifest_database_registration_rolls_back_all_entries_on_conflict() -> None:
    existing_entry = manifest_entry(
        content_sha256="b" * 64,
        crc64ecma="42",
        source_key="incoming/warehouse-002/high.png",
        version_id="cos-version-high-002",
    )
    register_media_manifest(
        payload={"objects": [existing_entry], "schema_version": 1},
        client=EntryAwareFakeCosClient((existing_entry,)),
        bucket="annotation-1250000000",
    )
    new_entry = manifest_entry(source_key="incoming/warehouse-003/high.png")
    conflicting_entry = {
        **existing_entry,
        "content_sha256": "c" * 64,
        "version_id": "cos-version-high-002-replaced",
    }

    with pytest.raises(MediaManifestConflict):
        register_media_manifest(
            payload={"objects": [new_entry, conflicting_entry], "schema_version": 1},
            client=EntryAwareFakeCosClient((new_entry, conflicting_entry)),
            bucket="annotation-1250000000",
        )

    assert not MediaObjectRegistration.objects.filter(source_key=new_entry["source_key"]).exists()
    assert (
        MediaObjectRegistration.objects.get(source_key=existing_entry["source_key"]).version_id
        == existing_entry["version_id"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_key", 1),
        ("version_id", 1),
        ("content_sha256", 1),
        ("crc64ecma", 1),
        ("format", 1),
    ],
)
def test_manifest_registration_rejects_non_string_entry_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(MediaManifestInvalid):
        register_media_manifest(
            payload=manifest_payload(**{field: value}),
            client=FakeCosClient(),
            bucket="annotation-1250000000",
        )

    assert MediaObjectRegistration.objects.count() == 0


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
    client = FakeCosClient(header_overrides=header_overrides)

    with pytest.raises(MediaManifestConflict):
        register_media_manifest(
            payload=manifest_payload(),
            client=client,
            bucket="annotation-1250000000",
        )

    assert MediaObjectRegistration.objects.count() == 0


def test_manifest_registration_rejects_cos_media_format_mismatch() -> None:
    client = FakeCosClient(header_overrides={"Content-Type": "image/jpeg"})

    with pytest.raises(MediaManifestConflict):
        register_media_manifest(
            payload=manifest_payload(),
            client=client,
            bucket="annotation-1250000000",
        )

    assert MediaObjectRegistration.objects.count() == 0
