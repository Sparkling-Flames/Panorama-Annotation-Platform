from __future__ import annotations

import importlib
from dataclasses import replace
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
    assert first_result.created_media_variant_ids == frozenset(
        variant.media_variant_id for variant in first_result.media_variants
    )
    assert second_result.created_asset is False
    assert second_result.created_media_variant_ids == frozenset()
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


def test_pap_mid_sc_003_equirectangular_variants_are_accepted() -> None:
    module = ingestion()

    preview = module.preview_import(import_plan())

    assert {(variant.width, variant.height) for variant in preview.plan.variants} == {
        (4096, 2048),
        (2048, 1024),
    }


def test_pap_mid_sc_004_skybox_face_sets_are_rejected_with_a_stable_code() -> None:
    module = ingestion()
    original = import_plan()
    skybox_faces = tuple(
        replace(
            original.variants[0],
            source_key=f"cos://incoming/warehouse-001/face-{index}.png",
            content_sha256=f"{index + 1:x}" * 64,
            width=1024,
            height=1024,
        )
        for index in range(6)
    )

    with pytest.raises(ValidationError) as rejected:
        module.preview_import(replace(original, variants=skybox_faces))

    assert getattr(rejected.value, "code", None) == "media_skybox_not_supported"


def test_media_import_rejects_non_equirectangular_images_with_a_stable_code() -> None:
    module = ingestion()
    original = import_plan()
    non_equirectangular = replace(
        original,
        variants=(
            replace(original.variants[0], width=4000, height=1500),
            replace(original.variants[1], width=2000, height=750),
        ),
    )

    with pytest.raises(ValidationError) as rejected:
        module.preview_import(non_equirectangular)

    assert getattr(rejected.value, "code", None) == "media_panorama_aspect_ratio_invalid"


def test_media_import_rejects_unsupported_format_with_a_stable_code() -> None:
    module = ingestion()
    original = import_plan()
    invalid_plan = replace(
        original,
        variants=(replace(original.variants[0], format="webp"), original.variants[1]),
    )

    with pytest.raises(ValidationError) as rejected:
        module.preview_import(invalid_plan)

    assert getattr(rejected.value, "code", None) == "media_format_unsupported"


def test_pap_mid_sc_013_incompatible_variant_mapping_is_rejected_with_a_stable_code() -> None:
    module = ingestion()
    original = import_plan()
    incompatible_plan = replace(
        original,
        variants=(
            original.variants[0],
            replace(original.variants[1], coordinate_mapping="cropped"),
        ),
    )

    with pytest.raises(ValidationError) as rejected:
        module.preview_import(incompatible_plan)

    assert getattr(rejected.value, "code", None) == "media_variant_mapping_incompatible"


def test_media_import_rejects_non_proportional_variants_with_the_mapping_code() -> None:
    module = ingestion()
    original = import_plan()
    incompatible_plan = replace(
        original,
        variants=(original.variants[0], replace(original.variants[1], width=3072)),
    )

    with pytest.raises(ValidationError) as rejected:
        module.preview_import(incompatible_plan)

    assert getattr(rejected.value, "code", None) == "media_variant_mapping_incompatible"


def test_media_import_allows_a_new_immutable_variant_for_an_existing_role() -> None:
    module = ingestion()
    models = media_models()
    original_plan = import_plan()
    original_result = module.publish_import(module.preview_import(original_plan))
    replacement_high_resolution = replace(
        original_plan.variants[0],
        source_key="cos://incoming/warehouse-001/high-v2.png",
        object_version="cos-version-high-002",
        content_sha256="c" * 64,
        content_crc64ecma="42",
    )
    replacement_plan = replace(
        original_plan,
        variants=(replacement_high_resolution, original_plan.variants[1]),
    )

    replacement_result = module.publish_import(module.preview_import(replacement_plan))

    assert replacement_result.created_asset is False
    assert replacement_result.asset == original_result.asset
    assert (
        models.MediaVariant.objects.filter(
            asset=original_result.asset,
            role=models.MediaVariant.Role.HIGH_RESOLUTION,
        ).count()
        == 2
    )
    assert models.MediaVariant.objects.filter(asset=original_result.asset).count() == 3
    assert replacement_result.media_variants[0].source_key == replacement_high_resolution.source_key
    assert replacement_result.media_variants[0].published_at is not None


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


def test_pap_mid_sc_006_explicit_new_annotation_round_reuses_published_asset() -> None:
    module = ingestion()
    models = media_models()
    original_result = module.publish_import(module.preview_import(import_plan()))

    new_round_result = module.publish_import(
        module.preview_import(import_plan(create_annotation_round=True))
    )

    assert new_round_result.created_asset is False
    assert new_round_result.asset.asset_id == original_result.asset.asset_id
    assert new_round_result.create_annotation_round is True
    assert models.Asset.objects.count() == 1
    assert models.MediaVariant.objects.count() == 2


def test_media_import_rolls_back_when_an_existing_source_key_has_different_metadata() -> None:
    module = ingestion()
    models = media_models()
    asset = models.Asset.objects.create(source_key=ASSET_SOURCE_KEY)
    existing_compressed = models.MediaVariant.objects.create(
        asset=asset,
        source_key=COMPRESSED_SOURCE_KEY,
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

    with pytest.raises(module.MediaSourceKeyConflict):
        module.publish_import(module.preview_import(import_plan()))

    assert models.Asset.objects.count() == 1
    assert list(models.MediaVariant.objects.values_list("source_key", flat=True)) == [
        existing_compressed.source_key
    ]
