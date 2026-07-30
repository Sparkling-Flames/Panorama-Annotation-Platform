from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.core.exceptions import ValidationError

pytestmark = pytest.mark.django_db

HIGH_RESOLUTION_HASH = "a" * 64
COMPRESSED_HASH = "b" * 64
ASSET_SOURCE_KEY = "panoramas/warehouse-001"
HIGH_RESOLUTION_SOURCE_KEY = "cos://incoming/warehouse-001/high.png"
COMPRESSED_SOURCE_KEY = "cos://incoming/warehouse-001/compressed.jpg"


def ingestion() -> Any:
    return importlib.import_module("media.ingestion")


def media_models() -> Any:
    return importlib.import_module("media.models")


def import_plan(*, create_annotation_round: bool = False) -> Any:
    module = ingestion()
    return module.MediaImportPlan(
        asset_source_key=ASSET_SOURCE_KEY,
        create_annotation_round=create_annotation_round,
        variants=(
            module.MediaVariantCandidate(
                source_key=HIGH_RESOLUTION_SOURCE_KEY,
                object_version="cos-version-high-001",
                content_sha256=HIGH_RESOLUTION_HASH,
                content_length=8_388_608,
                content_crc64ecma="12345678901234567890",
                width=4096,
                height=2048,
                format="png",
                role="high_resolution",
            ),
            module.MediaVariantCandidate(
                source_key=COMPRESSED_SOURCE_KEY,
                object_version="cos-version-compressed-001",
                content_sha256=COMPRESSED_HASH,
                content_length=1_048_576,
                content_crc64ecma="1234567890",
                width=2048,
                height=1024,
                format="jpeg",
                role="compressed",
            ),
        ),
    )


def test_pap_mid_sc_005_same_source_keys_and_hashes_are_idempotent() -> None:
    module = ingestion()
    models = media_models()
    plan = import_plan()

    first_preview = module.preview_import(plan)
    assert models.Asset.objects.count() == 0
    assert models.MediaVariant.objects.count() == 0
    first_result = module.publish_import(first_preview)

    second_preview = module.preview_import(plan)
    second_result = module.publish_import(second_preview)

    assert first_result.created_asset is True
    assert second_result.created_asset is False
    assert second_result.asset.asset_id == first_result.asset.asset_id
    assert models.Asset.objects.count() == 1
    assert models.MediaVariant.objects.count() == 2
    assert all(variant.published_at is not None for variant in second_result.media_variants)
    assert {
        (variant.source_key, variant.content_sha256) for variant in second_result.media_variants
    } == {
        (HIGH_RESOLUTION_SOURCE_KEY, HIGH_RESOLUTION_HASH),
        (COMPRESSED_SOURCE_KEY, COMPRESSED_HASH),
    }


def test_media_import_rolls_back_when_a_variant_cannot_be_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ingestion()
    models = media_models()
    publish = models.MediaVariant.publish

    def fail_compressed_variant(variant: Any) -> None:
        if variant.role == models.MediaVariant.Role.COMPRESSED:
            raise ValidationError("compressed publication failed")
        publish(variant)

    monkeypatch.setattr(models.MediaVariant, "publish", fail_compressed_variant)

    with pytest.raises(module.ImportConflict):
        module.publish_import(module.preview_import(import_plan()))

    assert models.Asset.objects.count() == 0
    assert models.MediaVariant.objects.count() == 0


def test_pap_mid_sc_007_cancelling_preview_does_not_publish_assets_or_task_intent() -> None:
    module = ingestion()
    models = media_models()

    preview = module.preview_import(import_plan(create_annotation_round=True))
    cancelled_preview = module.cancel_preview(preview)

    assert cancelled_preview.annotation_round_request is None
    with pytest.raises(module.PreviewCancelled):
        module.publish_import(cancelled_preview)
    assert models.Asset.objects.count() == 0
    assert models.MediaVariant.objects.count() == 0


def test_pap_mid_sc_006_explicit_new_annotation_round_reuses_published_asset() -> None:
    module = ingestion()
    models = media_models()
    original_result = module.publish_import(module.preview_import(import_plan()))

    new_round_result = module.publish_import(
        module.preview_import(import_plan(create_annotation_round=True))
    )

    assert new_round_result.created_asset is False
    assert new_round_result.asset.asset_id == original_result.asset.asset_id
    assert new_round_result.annotation_round_request is not None
    assert new_round_result.annotation_round_request.asset_id == original_result.asset.asset_id
    assert models.Asset.objects.count() == 1
    assert models.MediaVariant.objects.count() == 2


def test_media_import_rolls_back_a_new_first_variant_when_the_second_role_conflicts() -> None:
    module = ingestion()
    models = media_models()
    asset = models.Asset.objects.create(source_key=ASSET_SOURCE_KEY)
    existing_compressed = models.MediaVariant.objects.create(
        asset=asset,
        source_key="cos://incoming/warehouse-001/existing-compressed.jpg",
        object_version="cos-version-existing-compressed-001",
        content_sha256="c" * 64,
        content_length=1_048_576,
        content_crc64ecma="1234567890",
        width=2048,
        height=1024,
        format=models.MediaVariant.Format.JPEG,
        role=models.MediaVariant.Role.COMPRESSED,
        coordinate_mapping=models.MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    existing_compressed.publish()

    with pytest.raises(module.ImportConflict):
        module.publish_import(module.preview_import(import_plan()))

    assert models.Asset.objects.count() == 1
    assert list(models.MediaVariant.objects.values_list("source_key", flat=True)) == [
        existing_compressed.source_key
    ]
