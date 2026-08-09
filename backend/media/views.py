from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from identity.authorization import ResourceNotFound
from identity.http import error_response, request_json, require_admin, require_worker
from work.models import Assignment, Task, WorkBatch
from work.services import create_manual_annotation_round, get_owned_assignment

from .catalog import (
    CosCatalogUnavailable,
    MediaCandidateIntegrityConflict,
    MediaCandidateInvalid,
    MediaCandidateNotFound,
    MediaCatalogCandidate,
    get_cos_catalog,
)
from .ingestion import (
    SHA256_PATTERN,
    ImportConflict,
    ImportPreview,
    MediaImportPlan,
    MediaVariantCandidate,
    StoredPreviewError,
    cancel_stored_preview,
    preview_import,
    publish_stored_preview,
    store_import_preview,
)
from .models import MediaImportPreview, MediaVariant

IMPORT_REQUEST_FIELDS = {
    "asset_source_key",
    "compressed_source_key",
    "create_annotation_round",
    "high_resolution_source_key",
}
PUBLICATION_REQUEST_FIELDS = {"expected_plan_sha256", "preview_id"}


def _candidate_matches_variant(
    candidate: MediaCatalogCandidate,
    variant: MediaVariant,
) -> bool:
    return (
        candidate.source_key == variant.source_key
        and candidate.version_id == variant.object_version
        and candidate.content_sha256 == variant.content_sha256
        and candidate.content_length == variant.content_length
        and candidate.crc64ecma == variant.content_crc64ecma
        and candidate.width == variant.width
        and candidate.height == variant.height
        and candidate.format == variant.format
    )


@require_GET
def worker_assignment_media_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        assignment = get_owned_assignment(actor=actor, assignment_id=assignment_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    if (
        assignment.batch.status != WorkBatch.Status.OPEN
        or assignment.task.status != Task.Status.PUBLISHED
        or assignment.work_state == Assignment.WorkState.REVOKED
    ):
        return error_response("assignment_media_unavailable", status=409)

    variants = sorted(
        assignment.task.allowed_media_variants.filter(published_at__isnull=False),
        key=lambda variant: variant.role != MediaVariant.Role.COMPRESSED,
    )
    expires_at = timezone.now() + timedelta(seconds=settings.COS_SIGNED_URL_SECONDS)
    try:
        catalog = get_cos_catalog()
    except CosCatalogUnavailable:
        return error_response("cos_unavailable", status=503)

    available: list[dict[str, object]] = []
    unavailable_roles: list[str] = []
    temporary_failure = False
    for variant in variants:
        try:
            candidate = catalog.get_candidate(source_key=variant.source_key)
            if not _candidate_matches_variant(candidate, variant):
                raise MediaCandidateIntegrityConflict
        except CosCatalogUnavailable:
            temporary_failure = True
            unavailable_roles.append(variant.role)
            continue
        except (
            MediaCandidateIntegrityConflict,
            MediaCandidateInvalid,
            MediaCandidateNotFound,
        ):
            unavailable_roles.append(variant.role)
            continue
        available.append(
            {
                "coordinate_mapping": variant.coordinate_mapping,
                "height": variant.height,
                "media_variant_id": str(variant.media_variant_id),
                "role": variant.role,
                "url": candidate.preview_url,
                "width": variant.width,
            }
        )

    if not available:
        return error_response(
            "cos_unavailable" if temporary_failure else "image_unavailable",
            status=503 if temporary_failure else 409,
        )
    response = JsonResponse(
        {
            "assignment_id": str(assignment.assignment_id),
            "expires_at": expires_at.isoformat(),
            "unavailable_roles": unavailable_roles,
            "variants": available,
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
def candidates_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    marker = request.GET.get("marker")
    prefix = request.GET.get("prefix", "")
    if marker is not None and not marker:
        return error_response("invalid_media_marker", status=400)

    try:
        page = get_cos_catalog().list_candidates(prefix=prefix, marker=marker)
    except CosCatalogUnavailable:
        return error_response("cos_unavailable", status=503)
    except MediaCandidateIntegrityConflict:
        return error_response("media_candidate_integrity_conflict", status=409)

    return JsonResponse(
        {
            "candidates": [_candidate_payload(candidate) for candidate in page.candidates],
            "next_marker": page.next_marker,
        }
    )


@require_POST
def import_preview_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor

    try:
        preview, candidates = _preview_from_request(request)
        stored_preview = store_import_preview(
            preview=preview,
            actor=actor,
        )
    except _ImportRequestError as error:
        return error_response(error.code, status=error.status)

    return JsonResponse(
        _preview_payload(
            preview=preview,
            stored_preview=stored_preview,
            candidates=candidates,
        )
    )


@require_POST
def import_publish_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor

    catalog: Any | None = None

    def load_current_candidate(candidate: MediaVariantCandidate) -> MediaVariantCandidate:
        nonlocal catalog
        if catalog is None:
            catalog = get_cos_catalog()
        return _variant_candidate(
            catalog.get_candidate(source_key=candidate.source_key),
            role=candidate.role,
        )

    annotation_round: Task | None = None
    try:
        preview_id, expected_plan_sha256 = _publication_request(request)
        with transaction.atomic():
            publication = publish_stored_preview(
                preview_id=preview_id,
                actor=actor,
                expected_plan_sha256=expected_plan_sha256,
                candidate_loader=load_current_candidate,
            )
            if publication.create_annotation_round:
                annotation_round = create_manual_annotation_round(
                    source_preview_id=preview_id,
                    asset=publication.asset,
                    media_variants=publication.media_variants,
                )
    except _ImportRequestError as error:
        return error_response(error.code, status=error.status)
    except StoredPreviewError as error:
        return error_response(error.code, status=error.status)
    except CosCatalogUnavailable:
        return error_response("cos_unavailable", status=503)
    except MediaCandidateNotFound:
        return error_response("media_candidate_not_found", status=404)
    except MediaCandidateInvalid:
        return error_response("media_candidate_invalid", status=400)
    except MediaCandidateIntegrityConflict:
        return error_response("media_candidate_integrity_conflict", status=409)
    except ImportConflict as error:
        return error_response(type(error).code, status=409)
    except ValidationError as error:
        return error_response(error.code or "task_creation_failed", status=409)

    response_payload: dict[str, object] = {
        "annotation_round": (
            {
                "asset_id": str(annotation_round.asset_id),
                "mode": annotation_round.mode,
                "previous_task_id": (
                    str(annotation_round.previous_round_task_id)
                    if annotation_round.previous_round_task_id is not None
                    else None
                ),
                "status": annotation_round.status,
                "task_id": str(annotation_round.task_id),
            }
            if annotation_round is not None
            else None
        ),
        "asset_id": str(publication.asset.asset_id),
        "created_asset": publication.created_asset,
        "media_variants": [
            {
                "content_sha256": media_variant.content_sha256,
                "content_crc64ecma": media_variant.content_crc64ecma,
                "content_length": media_variant.content_length,
                "created": (
                    media_variant.media_variant_id in publication.created_media_variant_ids
                ),
                "format": media_variant.format,
                "height": media_variant.height,
                "media_variant_id": str(media_variant.media_variant_id),
                "object_version": media_variant.object_version,
                "role": media_variant.role,
                "source_key": media_variant.source_key,
                "width": media_variant.width,
            }
            for media_variant in publication.media_variants
        ],
    }
    return JsonResponse(response_payload, status=201 if publication.created_asset else 200)


@require_POST
def import_cancel_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor

    try:
        preview_id, expected_plan_sha256 = _publication_request(request)
        stored_preview = cancel_stored_preview(
            preview_id=preview_id,
            actor=actor,
            expected_plan_sha256=expected_plan_sha256,
        )
    except _ImportRequestError as error:
        return error_response(error.code, status=error.status)
    except StoredPreviewError as error:
        return error_response(error.code, status=error.status)
    return JsonResponse({"cancelled": True, "preview_id": str(stored_preview.preview_id)})


class _ImportRequestError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _publication_request(request: HttpRequest) -> tuple[UUID, str]:
    payload = request_json(request)
    if payload is None or set(payload) != PUBLICATION_REQUEST_FIELDS:
        raise _ImportRequestError("invalid_media_import_publication", 400)
    preview_id = payload.get("preview_id")
    expected_plan_sha256 = payload.get("expected_plan_sha256")
    if (
        not isinstance(preview_id, str)
        or not isinstance(expected_plan_sha256, str)
        or SHA256_PATTERN.fullmatch(expected_plan_sha256) is None
    ):
        raise _ImportRequestError("invalid_media_import_publication", 400)
    try:
        return UUID(preview_id), expected_plan_sha256
    except ValueError as error:
        raise _ImportRequestError("invalid_media_import_publication", 400) from error


def _preview_from_request(
    request: HttpRequest,
) -> tuple[ImportPreview, tuple[MediaCatalogCandidate, MediaCatalogCandidate]]:
    payload = request_json(request)
    if payload is None:
        raise _ImportRequestError("invalid_media_import", 400)
    if "skybox_face_source_keys" in payload:
        raise _ImportRequestError("media_skybox_not_supported", 400)
    if set(payload) != IMPORT_REQUEST_FIELDS:
        raise _ImportRequestError("invalid_media_import", 400)

    asset_source_key = payload.get("asset_source_key")
    high_resolution_source_key = payload.get("high_resolution_source_key")
    compressed_source_key = payload.get("compressed_source_key")
    create_annotation_round = payload.get("create_annotation_round")
    if (
        not isinstance(asset_source_key, str)
        or not isinstance(high_resolution_source_key, str)
        or not isinstance(compressed_source_key, str)
        or not isinstance(create_annotation_round, bool)
    ):
        raise _ImportRequestError("invalid_media_import", 400)

    try:
        catalog = get_cos_catalog()
        high_resolution = catalog.get_candidate(source_key=high_resolution_source_key)
        compressed = catalog.get_candidate(source_key=compressed_source_key)
        plan = MediaImportPlan(
            asset_source_key=asset_source_key,
            create_annotation_round=create_annotation_round,
            variants=(
                _variant_candidate(high_resolution, role="high_resolution"),
                _variant_candidate(compressed, role="compressed"),
            ),
        )
        return preview_import(plan), (high_resolution, compressed)
    except CosCatalogUnavailable as error:
        raise _ImportRequestError("cos_unavailable", 503) from error
    except MediaCandidateNotFound as error:
        raise _ImportRequestError("media_candidate_not_found", 404) from error
    except MediaCandidateInvalid as error:
        raise _ImportRequestError("media_candidate_invalid", 400) from error
    except MediaCandidateIntegrityConflict as error:
        raise _ImportRequestError("media_candidate_integrity_conflict", 409) from error
    except ValidationError as error:
        raise _ImportRequestError(
            getattr(error, "code", None) or "invalid_media_import",
            400,
        ) from error


def _variant_candidate(candidate: MediaCatalogCandidate, *, role: str) -> MediaVariantCandidate:
    return MediaVariantCandidate(
        source_key=candidate.source_key,
        object_version=candidate.version_id,
        content_sha256=candidate.content_sha256,
        content_length=candidate.content_length,
        content_crc64ecma=candidate.crc64ecma,
        width=candidate.width,
        height=candidate.height,
        format=candidate.format,
        role=role,
    )


def _candidate_payload(
    candidate: MediaCatalogCandidate, *, role: str | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "content_crc64ecma": candidate.crc64ecma,
        "content_length": candidate.content_length,
        "content_sha256": candidate.content_sha256,
        "coordinate_mapping": MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
        "format": candidate.format,
        "height": candidate.height,
        "object_version": candidate.version_id,
        "preview_url": candidate.preview_url,
        "source_key": candidate.source_key,
        "width": candidate.width,
    }
    if role is not None:
        payload["role"] = role
    return payload


def _preview_payload(
    *,
    preview: ImportPreview,
    stored_preview: MediaImportPreview,
    candidates: tuple[MediaCatalogCandidate, MediaCatalogCandidate],
) -> dict[str, object]:
    high_resolution, compressed = candidates
    return {
        "asset_source_key": preview.plan.asset_source_key,
        "asset_will_be_reused": preview.asset_will_be_reused,
        "create_annotation_round": preview.plan.create_annotation_round,
        "expires_at": stored_preview.expires_at.isoformat(),
        "plan_sha256": stored_preview.plan_sha256,
        "preview_id": str(stored_preview.preview_id),
        "variants": [
            _candidate_payload(high_resolution, role="high_resolution"),
            _candidate_payload(compressed, role="compressed"),
        ],
    }
