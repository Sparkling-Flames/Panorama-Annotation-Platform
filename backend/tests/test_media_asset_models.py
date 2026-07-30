from __future__ import annotations

import importlib
from typing import Any
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError

pytestmark = pytest.mark.django_db

HIGH_RESOLUTION_HASH = "a" * 64
COMPRESSED_HASH = "b" * 64
HIGH_RESOLUTION_VERSION = "cos-version-high-001"
HIGH_RESOLUTION_LENGTH = 8_388_608
HIGH_RESOLUTION_CRC64 = "12345678901234567890"
HIGH_RESOLUTION_SOURCE_KEY = "cos://incoming/panorama-001.high.png"
COMPRESSED_SOURCE_KEY = "cos://incoming/panorama-001.compressed.jpg"


def media_models() -> Any:
    return importlib.import_module("media.models")


def create_asset(*, source_key: str = "cos://incoming/panorama-001.png") -> Any:
    models = media_models()
    return models.Asset.objects.create(source_key=source_key)


def new_variant(*, asset: Any, **overrides: object) -> Any:
    models = media_models()
    values: dict[str, object] = {
        "asset": asset,
        "source_key": HIGH_RESOLUTION_SOURCE_KEY,
        "content_sha256": HIGH_RESOLUTION_HASH,
        "object_version": HIGH_RESOLUTION_VERSION,
        "content_length": HIGH_RESOLUTION_LENGTH,
        "content_crc64ecma": HIGH_RESOLUTION_CRC64,
        "coordinate_mapping": models.MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
        "format": models.MediaVariant.Format.PNG,
        "height": 2048,
        "role": models.MediaVariant.Role.HIGH_RESOLUTION,
        "width": 4096,
    }
    values.update(overrides)
    return models.MediaVariant(**values)


def test_pap_mid_sc_001_asset_and_variant_ids_are_stable_and_non_reused() -> None:
    models = media_models()
    asset = create_asset()
    second_asset = create_asset(source_key="cos://incoming/panorama-002.png")

    assert UUID(str(asset.asset_id)).version == 4
    assert asset.asset_id != second_asset.asset_id
    original_asset_id = asset.asset_id
    asset.source_key = "cos://curated/panorama-001.png"
    asset.save(update_fields=["source_key"])
    asset.refresh_from_db()
    assert asset.asset_id == original_asset_id

    high_resolution = new_variant(asset=asset)
    compressed = new_variant(
        asset=asset,
        content_sha256=COMPRESSED_HASH,
        format=models.MediaVariant.Format.JPEG,
        role=models.MediaVariant.Role.COMPRESSED,
        source_key=COMPRESSED_SOURCE_KEY,
    )
    high_resolution.full_clean()
    compressed.full_clean()
    high_resolution.save()
    compressed.save()

    assert UUID(str(high_resolution.media_variant_id)).version == 4
    assert high_resolution.media_variant_id != compressed.media_variant_id
    assert high_resolution.asset_id == compressed.asset_id == asset.asset_id


def test_media_variant_contract_requires_content_metadata_and_normalized_mapping() -> None:
    models = media_models()
    asset = create_asset()

    variant = new_variant(asset=asset)
    variant.full_clean()
    assert variant.content_sha256 == HIGH_RESOLUTION_HASH
    assert variant.object_version == HIGH_RESOLUTION_VERSION
    assert variant.content_length == HIGH_RESOLUTION_LENGTH
    assert variant.content_crc64ecma == HIGH_RESOLUTION_CRC64
    assert (variant.width, variant.height) == (4096, 2048)
    assert variant.format == models.MediaVariant.Format.PNG
    assert variant.role == models.MediaVariant.Role.HIGH_RESOLUTION
    assert variant.coordinate_mapping == models.MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY

    invalid_variants = [
        new_variant(asset=asset, content_sha256="not-a-sha256"),
        new_variant(asset=asset, object_version=""),
        new_variant(asset=asset, content_length=0),
        new_variant(asset=asset, content_crc64ecma="not-a-crc64"),
        new_variant(asset=asset, width=0),
        new_variant(asset=asset, height=0),
        new_variant(asset=asset, format="webp"),
        new_variant(asset=asset, role="original"),
        new_variant(asset=asset, coordinate_mapping="pixel-space"),
    ]
    for invalid_variant in invalid_variants:
        with pytest.raises(ValidationError):
            invalid_variant.full_clean()


def test_media_variant_persists_registered_cos_object_identity() -> None:
    asset = create_asset()
    variant = new_variant(
        asset=asset,
        object_version="cos-version-high-001",
        content_length=8_388_608,
        content_crc64ecma="12345678901234567890",
    )

    variant.save()

    assert variant.object_version == "cos-version-high-001"
    assert variant.content_length == 8_388_608
    assert variant.content_crc64ecma == "12345678901234567890"


def test_media_variant_rejects_non_proportional_normalized_identity_mapping() -> None:
    models = media_models()
    asset = create_asset()
    new_variant(asset=asset).save()
    non_proportional_variant = new_variant(
        asset=asset,
        content_sha256=COMPRESSED_HASH,
        format=models.MediaVariant.Format.JPEG,
        height=2048,
        role=models.MediaVariant.Role.COMPRESSED,
        source_key=COMPRESSED_SOURCE_KEY,
        width=2048,
    )

    with pytest.raises(ValidationError, match="proportional"):
        non_proportional_variant.save()


def test_published_media_variant_rejects_hash_drift_and_content_contract_mutation() -> None:
    models = media_models()
    asset = create_asset()
    replacement_asset = create_asset(source_key="cos://incoming/panorama-003.png")
    variant = new_variant(asset=asset)
    variant.full_clean()
    variant.save()
    variant.publish()
    variant.refresh_from_db()
    assert variant.published_at is not None

    prohibited_changes = {
        "asset": replacement_asset,
        "content_sha256": COMPRESSED_HASH,
        "object_version": "cos-version-high-002",
        "content_length": HIGH_RESOLUTION_LENGTH + 1,
        "content_crc64ecma": "1",
        "coordinate_mapping": "pixel-space",
        "format": models.MediaVariant.Format.JPEG,
        "height": 1024,
        "role": models.MediaVariant.Role.COMPRESSED,
        "source_key": COMPRESSED_SOURCE_KEY,
        "width": 2048,
    }
    for field, value in prohibited_changes.items():
        setattr(variant, field, value)
        with pytest.raises(ValidationError, match="immutable"):
            variant.save()
        variant.refresh_from_db()


def test_changed_content_requires_a_new_variant_id() -> None:
    models = media_models()
    asset = create_asset()
    original = new_variant(asset=asset)
    original.save()
    original.publish()

    replacement = new_variant(
        asset=asset,
        content_sha256=COMPRESSED_HASH,
        source_key="cos://incoming/panorama-001.high.v2.png",
    )
    replacement.save()
    replacement.publish()

    assert replacement.media_variant_id != original.media_variant_id
    assert models.MediaVariant.objects.filter(asset=asset, role=original.role).count() == 2
