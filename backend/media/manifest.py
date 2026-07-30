from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Final

from django.db import transaction
from qcloud_cos.cos_exception import CosClientError, CosServiceError

from .models import MediaObjectRegistration, MediaVariant

MANIFEST_FIELDS: Final = {"objects", "schema_version"}
ENTRY_FIELDS: Final = {
    "content_length",
    "content_sha256",
    "crc64ecma",
    "format",
    "height",
    "source_key",
    "version_id",
    "width",
}


class MediaManifestInvalid(Exception):
    pass


class MediaManifestConflict(Exception):
    pass


class MediaManifestUnavailable(Exception):
    pass


@dataclass(frozen=True)
class MediaManifestEntry:
    source_key: str
    version_id: str
    content_sha256: str
    content_length: int
    crc64ecma: str
    width: int
    height: int
    format: str


@transaction.atomic
def register_media_manifest(
    *,
    payload: object,
    client: Any,
    bucket: str,
) -> tuple[MediaObjectRegistration, ...]:
    entries = _parse_manifest(payload)
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
    except (CosClientError, CosServiceError) as error:
        raise MediaManifestUnavailable("COS bucket versioning could not be verified.") from error
    if not isinstance(versioning, dict) or versioning.get("Status") != "Enabled":
        raise MediaManifestInvalid("COS bucket versioning must be enabled.")

    manifest_sha256 = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    registrations = []
    for entry in entries:
        _verify_cos_object(entry=entry, client=client, bucket=bucket)
        registrations.append(_register_entry(entry=entry, manifest_sha256=manifest_sha256))
    return tuple(registrations)


def _parse_manifest(payload: object) -> tuple[MediaManifestEntry, ...]:
    if not isinstance(payload, dict) or set(payload) != MANIFEST_FIELDS:
        raise MediaManifestInvalid("The media manifest has an invalid top-level schema.")
    objects = payload.get("objects")
    if payload.get("schema_version") != 1 or not isinstance(objects, list) or not objects:
        raise MediaManifestInvalid("The media manifest must contain version 1 objects.")

    entries = []
    for value in objects:
        if not isinstance(value, dict) or set(value) != ENTRY_FIELDS:
            raise MediaManifestInvalid("A media manifest entry has an invalid schema.")
        try:
            entry = MediaManifestEntry(**value)
        except TypeError as error:
            raise MediaManifestInvalid("A media manifest entry has invalid values.") from error
        _validate_entry(entry)
        entries.append(entry)
    if len({entry.source_key for entry in entries}) != len(entries):
        raise MediaManifestInvalid("Media manifest source keys must be unique.")
    return tuple(entries)


def _validate_entry(entry: MediaManifestEntry) -> None:
    if not entry.source_key.strip() or not entry.version_id.strip():
        raise MediaManifestInvalid("Source key and version ID are required.")
    if re.fullmatch(r"[0-9a-f]{64}", entry.content_sha256) is None:
        raise MediaManifestInvalid("A lowercase SHA-256 digest is required.")
    if re.fullmatch(r"[0-9]{1,20}", entry.crc64ecma) is None:
        raise MediaManifestInvalid("A COS CRC64 value is required.")
    if int(entry.crc64ecma) > 2**64 - 1:
        raise MediaManifestInvalid("The COS CRC64 value is out of range.")
    if (
        type(entry.content_length) is not int
        or type(entry.width) is not int
        or type(entry.height) is not int
        or min(entry.content_length, entry.width, entry.height) <= 0
    ):
        raise MediaManifestInvalid("Positive content length and dimensions are required.")
    if entry.format not in MediaVariant.Format.values:
        raise MediaManifestInvalid("Only PNG and JPEG manifest entries are supported.")


def _verify_cos_object(*, entry: MediaManifestEntry, client: Any, bucket: str) -> None:
    try:
        headers = client.head_object(
            Bucket=bucket,
            Key=entry.source_key,
            VersionId=entry.version_id,
        )
    except CosServiceError as error:
        if error.get_status_code() == 404:
            raise MediaManifestConflict(
                "The registered COS object version does not exist."
            ) from error
        raise MediaManifestUnavailable("COS object metadata is unavailable.") from error
    except CosClientError as error:
        raise MediaManifestUnavailable("COS object metadata is unavailable.") from error
    if not isinstance(headers, dict):
        raise MediaManifestConflict("COS returned invalid object metadata.")

    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    expected = {
        "content-length": str(entry.content_length),
        "x-cos-hash-crc64ecma": entry.crc64ecma,
        "x-cos-version-id": entry.version_id,
    }
    if any(normalized.get(name) != value for name, value in expected.items()):
        raise MediaManifestConflict("COS object integrity metadata does not match the manifest.")

    optional_metadata = {
        "x-cos-meta-content-sha256": entry.content_sha256,
        "x-cos-meta-height": str(entry.height),
        "x-cos-meta-width": str(entry.width),
    }
    if any(
        name in normalized and normalized[name] != value
        for name, value in optional_metadata.items()
    ):
        raise MediaManifestConflict("COS custom metadata does not match the trusted manifest.")


def _register_entry(
    *,
    entry: MediaManifestEntry,
    manifest_sha256: str,
) -> MediaObjectRegistration:
    values = asdict(entry)
    existing = MediaObjectRegistration.objects.filter(source_key=entry.source_key).first()
    if existing is not None:
        if any(getattr(existing, field) != value for field, value in values.items()):
            raise MediaManifestConflict("The COS source key is already registered differently.")
        return existing
    return MediaObjectRegistration.objects.create(
        **values,
        manifest_sha256=manifest_sha256,
    )
