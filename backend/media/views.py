from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from identity.models import User
from work.models import Task
from work.services import create_manual_annotation_round

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


def error_response(code: str, *, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code}}, status=status)


def request_json(request: HttpRequest) -> dict[str, Any] | None:
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def require_admin(request: HttpRequest) -> JsonResponse | None:
    user = getattr(request, "user", None)
    if not isinstance(user, AbstractBaseUser) or not user.is_authenticated:
        return error_response("authentication_required", status=401)
    if not isinstance(user, User) or user.role != User.Role.ADMIN:
        return error_response("admin_required", status=403)
    return None


@require_GET
def candidates_view(request: HttpRequest) -> JsonResponse:
    denied = require_admin(request)
    if denied is not None:
        return denied
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
    denied = require_admin(request)
    if denied is not None:
        return denied

    try:
        preview, candidates = _preview_from_request(request)
        stored_preview = store_import_preview(
            preview=preview,
            actor=cast(User, request.user),
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
    denied = require_admin(request)
    if denied is not None:
        return denied

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
                actor=cast(User, request.user),
                expected_plan_sha256=expected_plan_sha256,
                candidate_loader=load_current_candidate,
            )
            if publication.annotation_round_request is not None:
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
    denied = require_admin(request)
    if denied is not None:
        return denied

    try:
        preview_id, expected_plan_sha256 = _publication_request(request)
        stored_preview = cancel_stored_preview(
            preview_id=preview_id,
            actor=cast(User, request.user),
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
