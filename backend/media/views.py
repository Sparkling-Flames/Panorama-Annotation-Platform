from __future__ import annotations

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from identity.http import error_response, request_json, require_admin

from .catalog import (
    CosCatalogUnavailable,
    MediaCandidateIntegrityConflict,
    MediaCandidateInvalid,
    MediaCandidateNotFound,
    MediaCatalogCandidate,
    get_cos_catalog,
)
from .import_requests import ImportRequestError, publication_request, variant_candidate
from .ingestion import (
    ImportPreview,
    MediaImportPlan,
    StoredPreviewError,
    cancel_stored_preview,
    preview_import,
    store_import_preview,
)
from .models import MediaImportPreview, MediaVariant

IMPORT_REQUEST_FIELDS = {
    "asset_source_key",
    "compressed_source_key",
    "create_annotation_round",
    "high_resolution_source_key",
}


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
    except ImportRequestError as error:
        return error_response(error.code, status=error.status)

    return JsonResponse(
        _preview_payload(
            preview=preview,
            stored_preview=stored_preview,
            candidates=candidates,
        )
    )


@require_POST
def import_cancel_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor

    try:
        preview_id, expected_plan_sha256 = publication_request(request)
        stored_preview = cancel_stored_preview(
            preview_id=preview_id,
            actor=actor,
            expected_plan_sha256=expected_plan_sha256,
        )
    except ImportRequestError as error:
        return error_response(error.code, status=error.status)
    except StoredPreviewError as error:
        return error_response(error.code, status=error.status)
    return JsonResponse({"cancelled": True, "preview_id": str(stored_preview.preview_id)})


def _preview_from_request(
    request: HttpRequest,
) -> tuple[ImportPreview, tuple[MediaCatalogCandidate, MediaCatalogCandidate]]:
    payload = request_json(request)
    if payload is None:
        raise ImportRequestError("invalid_media_import", 400)
    if "skybox_face_source_keys" in payload:
        raise ImportRequestError("media_skybox_not_supported", 400)
    if set(payload) != IMPORT_REQUEST_FIELDS:
        raise ImportRequestError("invalid_media_import", 400)

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
        raise ImportRequestError("invalid_media_import", 400)

    try:
        catalog = get_cos_catalog()
        high_resolution = catalog.get_candidate(source_key=high_resolution_source_key)
        compressed = catalog.get_candidate(source_key=compressed_source_key)
        plan = MediaImportPlan(
            asset_source_key=asset_source_key,
            create_annotation_round=create_annotation_round,
            variants=(
                variant_candidate(high_resolution, role="high_resolution"),
                variant_candidate(compressed, role="compressed"),
            ),
        )
        return preview_import(plan), (high_resolution, compressed)
    except CosCatalogUnavailable as error:
        raise ImportRequestError("cos_unavailable", 503) from error
    except MediaCandidateNotFound as error:
        raise ImportRequestError("media_candidate_not_found", 404) from error
    except MediaCandidateInvalid as error:
        raise ImportRequestError("media_candidate_invalid", 400) from error
    except MediaCandidateIntegrityConflict as error:
        raise ImportRequestError("media_candidate_integrity_conflict", 409) from error
    except ValidationError as error:
        raise ImportRequestError(
            getattr(error, "code", None) or "invalid_media_import",
            400,
        ) from error


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
