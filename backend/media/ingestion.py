from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from identity.models import User

from .models import Asset, MediaImportPreview, MediaVariant

SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_VARIANT_ROLES: Final = frozenset(
    {
        MediaVariant.Role.HIGH_RESOLUTION,
        MediaVariant.Role.COMPRESSED,
    }
)
IMPORT_PREVIEW_TTL: Final = timedelta(minutes=15)


class ImportConflict(ValidationError):
    code = "media_import_conflict"


class MediaSourceKeyConflict(ImportConflict):
    code = "media_source_key_conflict"


class StoredPreviewError(Exception):
    code = "media_import_preview_conflict"
    status = 409


class StoredPreviewNotFound(StoredPreviewError):
    code = "media_import_preview_not_found"
    status = 404


class StoredPreviewExpired(StoredPreviewError):
    code = "media_import_preview_expired"


class StoredPreviewCancelled(StoredPreviewError):
    code = "media_import_preview_cancelled"


class StoredPreviewPublished(StoredPreviewError):
    code = "media_import_preview_published"


class ImportIntegrityConflict(StoredPreviewError):
    code = "media_import_integrity_conflict"


@dataclass(frozen=True)
class MediaVariantCandidate:
    source_key: str
    object_version: str
    content_sha256: str
    content_length: int
    content_crc64ecma: str
    width: int
    height: int
    format: str
    role: str
    coordinate_mapping: str = MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY


@dataclass(frozen=True)
class MediaImportPlan:
    asset_source_key: str
    variants: tuple[MediaVariantCandidate, ...]
    create_annotation_round: bool = False


@dataclass(frozen=True)
class ImportPreview:
    plan: MediaImportPlan
    asset_will_be_reused: bool


@dataclass(frozen=True)
class ImportPublication:
    asset: Asset
    media_variants: tuple[MediaVariant, ...]
    created_asset: bool
    created_media_variant_ids: frozenset[UUID]
    create_annotation_round: bool


CandidateLoader = Callable[[MediaVariantCandidate], MediaVariantCandidate]


def preview_import(plan: MediaImportPlan) -> ImportPreview:
    _validate_plan(plan)
    return ImportPreview(
        plan=plan,
        asset_will_be_reused=Asset.objects.filter(source_key=plan.asset_source_key).exists(),
    )


def store_import_preview(*, preview: ImportPreview, actor: User) -> MediaImportPreview:
    plan_payload, plan_sha256 = _serialize_plan(preview.plan)
    return MediaImportPreview.objects.create(
        actor=actor,
        plan=plan_payload,
        plan_sha256=plan_sha256,
        expires_at=timezone.now() + IMPORT_PREVIEW_TTL,
    )


@transaction.atomic
def cancel_stored_preview(
    *,
    preview_id: UUID,
    actor: User,
    expected_plan_sha256: str,
) -> MediaImportPreview:
    stored_preview = _owned_stored_preview(
        preview_id=preview_id,
        actor=actor,
        expected_plan_sha256=expected_plan_sha256,
    )
    if stored_preview.published_at is not None:
        raise StoredPreviewPublished
    if stored_preview.cancelled_at is None:
        stored_preview.cancelled_at = timezone.now()
        stored_preview.save(update_fields=["cancelled_at"])
    return stored_preview


@transaction.atomic
def publish_stored_preview(
    *,
    preview_id: UUID,
    actor: User,
    expected_plan_sha256: str,
    candidate_loader: CandidateLoader,
) -> ImportPublication:
    stored_preview = _owned_stored_preview(
        preview_id=preview_id,
        actor=actor,
        expected_plan_sha256=expected_plan_sha256,
    )
    plan = _plan_from_stored_preview(stored_preview)
    if stored_preview.published_at is not None:
        return _publication_from_stored_preview(stored_preview, plan=plan)
    if stored_preview.cancelled_at is not None:
        raise StoredPreviewCancelled
    if stored_preview.expires_at <= timezone.now():
        raise StoredPreviewExpired

    current_variants = tuple(candidate_loader(candidate) for candidate in plan.variants)
    if current_variants != plan.variants:
        raise ImportIntegrityConflict

    publication = publish_import(
        ImportPreview(
            plan=plan,
            asset_will_be_reused=Asset.objects.filter(source_key=plan.asset_source_key).exists(),
        )
    )
    stored_preview.published_asset = publication.asset
    stored_preview.publication_created_asset = publication.created_asset
    stored_preview.publication_created_media_variant_ids = sorted(
        str(media_variant_id) for media_variant_id in publication.created_media_variant_ids
    )
    stored_preview.published_at = timezone.now()
    stored_preview.save(
        update_fields=[
            "publication_created_asset",
            "publication_created_media_variant_ids",
            "published_asset",
            "published_at",
        ]
    )
    return publication


@transaction.atomic
def publish_import(preview: ImportPreview) -> ImportPublication:
    try:
        plan = preview.plan
        asset, created_asset = Asset.objects.get_or_create(source_key=plan.asset_source_key)
        asset = Asset.objects.select_for_update().get(pk=asset.pk)
        variant_results = tuple(
            _publish_variant(asset=asset, candidate=candidate) for candidate in plan.variants
        )
        media_variants = tuple(media_variant for media_variant, _created in variant_results)
    except ImportConflict:
        raise
    except (IntegrityError, ValidationError) as error:
        raise ImportConflict("Media import conflicts with existing data.") from error
    return ImportPublication(
        asset=asset,
        media_variants=media_variants,
        created_asset=created_asset,
        created_media_variant_ids=frozenset(
            media_variant.media_variant_id for media_variant, created in variant_results if created
        ),
        create_annotation_round=plan.create_annotation_round,
    )


def _validate_plan(plan: MediaImportPlan) -> None:
    if not isinstance(plan.asset_source_key, str) or not plan.asset_source_key.strip():
        raise ValidationError({"asset_source_key": "An asset source key is required."})
    if not isinstance(plan.create_annotation_round, bool):
        raise ValidationError({"create_annotation_round": "Must be a boolean."})
    if not plan.variants:
        raise ValidationError({"variants": "At least one media variant is required."})
    if len(plan.variants) == 6 and all(
        isinstance(candidate.width, int)
        and isinstance(candidate.height, int)
        and candidate.width == candidate.height
        for candidate in plan.variants
    ):
        raise ValidationError(
            "Skybox face sets are not supported; provide a stitched equirectangular panorama.",
            code="media_skybox_not_supported",
        )

    source_keys = [candidate.source_key for candidate in plan.variants]
    if len(source_keys) != len(set(source_keys)):
        raise ValidationError({"variants": "Each media variant source key must be unique."})
    roles = {candidate.role for candidate in plan.variants}
    if roles != REQUIRED_VARIANT_ROLES or len(plan.variants) != len(REQUIRED_VARIANT_ROLES):
        raise ValidationError(
            {"variants": "One high-resolution and one compressed variant are required."}
        )

    for candidate in plan.variants:
        _validate_candidate(candidate)

    reference = plan.variants[0]
    for candidate in plan.variants:
        if candidate.width * reference.height != candidate.height * reference.width:
            raise ValidationError(
                "Normalized identity media variants must retain proportional dimensions.",
                code="media_variant_mapping_incompatible",
            )
    for candidate in plan.variants:
        if candidate.width != candidate.height * 2:
            raise ValidationError(
                "A stitched equirectangular panorama must use a 2:1 aspect ratio.",
                code="media_panorama_aspect_ratio_invalid",
            )


def _validate_candidate(candidate: MediaVariantCandidate) -> None:
    if not isinstance(candidate.source_key, str) or not candidate.source_key.strip():
        raise ValidationError({"source_key": "A media variant source key is required."})
    if not isinstance(candidate.content_sha256, str) or not SHA256_PATTERN.fullmatch(
        candidate.content_sha256
    ):
        raise ValidationError({"content_sha256": "A lowercase SHA-256 hex digest is required."})
    if not isinstance(candidate.object_version, str) or not candidate.object_version.strip():
        raise ValidationError({"object_version": "A COS object version is required."})
    if not isinstance(candidate.content_length, int) or candidate.content_length <= 0:
        raise ValidationError({"content_length": "A positive content length is required."})
    if (
        not isinstance(candidate.content_crc64ecma, str)
        or re.fullmatch(r"[0-9]{1,20}", candidate.content_crc64ecma) is None
        or int(candidate.content_crc64ecma) > 2**64 - 1
    ):
        raise ValidationError({"content_crc64ecma": "A valid COS CRC64 value is required."})
    if not isinstance(candidate.width, int) or candidate.width <= 0:
        raise ValidationError({"width": "A positive width is required."})
    if not isinstance(candidate.height, int) or candidate.height <= 0:
        raise ValidationError({"height": "A positive height is required."})
    if candidate.format not in MediaVariant.Format.values:
        raise ValidationError("Unsupported media format.", code="media_format_unsupported")
    if candidate.role not in MediaVariant.Role.values:
        raise ValidationError({"role": "Unsupported media role."})
    if candidate.coordinate_mapping != MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY:
        raise ValidationError(
            "Only normalized identity mapping is supported.",
            code="media_variant_mapping_incompatible",
        )


def _serialize_plan(plan: MediaImportPlan) -> tuple[dict[str, Any], str]:
    canonical = json.dumps(asdict(plan), sort_keys=True, separators=(",", ":"))
    return json.loads(canonical), hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_from_stored_preview(stored_preview: MediaImportPreview) -> MediaImportPlan:
    try:
        canonical = json.dumps(stored_preview.plan, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != stored_preview.plan_sha256:
            raise StoredPreviewError
        payload = stored_preview.plan
        variants_payload = payload["variants"]
        if not isinstance(variants_payload, list):
            raise StoredPreviewError
        plan = MediaImportPlan(
            asset_source_key=payload["asset_source_key"],
            create_annotation_round=payload["create_annotation_round"],
            variants=tuple(MediaVariantCandidate(**candidate) for candidate in variants_payload),
        )
        _validate_plan(plan)
    except (KeyError, TypeError, ValidationError) as error:
        raise StoredPreviewError from error
    return plan


def _owned_stored_preview(
    *,
    preview_id: UUID,
    actor: User,
    expected_plan_sha256: str,
) -> MediaImportPreview:
    stored_preview = (
        MediaImportPreview.objects.select_for_update()
        .filter(preview_id=preview_id, actor=actor)
        .first()
    )
    if stored_preview is None:
        raise StoredPreviewNotFound
    if stored_preview.plan_sha256 != expected_plan_sha256:
        raise StoredPreviewError
    return stored_preview


def _publication_from_stored_preview(
    stored_preview: MediaImportPreview,
    *,
    plan: MediaImportPlan,
) -> ImportPublication:
    if stored_preview.published_asset is None or stored_preview.publication_created_asset is None:
        raise StoredPreviewError
    media_variants = tuple(
        MediaVariant.objects.get(
            asset=stored_preview.published_asset,
            source_key=candidate.source_key,
        )
        for candidate in plan.variants
    )
    return ImportPublication(
        asset=stored_preview.published_asset,
        media_variants=media_variants,
        created_asset=stored_preview.publication_created_asset,
        created_media_variant_ids=frozenset(
            UUID(value) for value in stored_preview.publication_created_media_variant_ids
        ),
        create_annotation_round=plan.create_annotation_round,
    )


def _publish_variant(
    *, asset: Asset, candidate: MediaVariantCandidate
) -> tuple[MediaVariant, bool]:
    existing = MediaVariant.objects.filter(source_key=candidate.source_key).first()
    if existing is None:
        media_variant = MediaVariant(
            asset=asset,
            source_key=candidate.source_key,
            object_version=candidate.object_version,
            content_sha256=candidate.content_sha256,
            content_length=candidate.content_length,
            content_crc64ecma=candidate.content_crc64ecma,
            width=candidate.width,
            height=candidate.height,
            format=candidate.format,
            role=candidate.role,
            coordinate_mapping=candidate.coordinate_mapping,
        )
        media_variant.save()
        media_variant.publish()
        return media_variant, True

    expected_values = {
        "asset_id": asset.asset_id,
        "content_crc64ecma": candidate.content_crc64ecma,
        "content_length": candidate.content_length,
        "content_sha256": candidate.content_sha256,
        "coordinate_mapping": candidate.coordinate_mapping,
        "format": candidate.format,
        "height": candidate.height,
        "object_version": candidate.object_version,
        "role": candidate.role,
        "width": candidate.width,
    }
    if any(getattr(existing, field) != value for field, value in expected_values.items()):
        raise MediaSourceKeyConflict(
            "An existing media source key has incompatible immutable metadata."
        )
    if existing.published_at is None:
        existing.publish()
    return existing, False
