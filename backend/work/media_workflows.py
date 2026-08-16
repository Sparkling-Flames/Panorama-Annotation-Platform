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
from identity.http import error_response, require_admin, require_production_worker
from media.catalog import (
    CosCatalogUnavailable,
    MediaCandidateIntegrityConflict,
    MediaCandidateInvalid,
    MediaCandidateNotFound,
    candidate_matches_variant,
    get_cos_catalog,
)
from media.import_requests import ImportRequestError, publication_request, variant_candidate
from media.ingestion import (
    ImportConflict,
    MediaVariantCandidate,
    StoredPreviewError,
    publish_stored_preview,
)
from media.models import MediaVariant

from .models import Assignment, OperationalIssue, Task, WorkBatch
from .operations import record_operational_issue
from .services import create_manual_annotation_round, get_owned_assignment


@require_GET
def worker_assignment_media_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_production_worker(request)
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
        record_operational_issue(
            actor=actor,
            assignment_id=assignment_id,
            kind=OperationalIssue.Kind.MEDIA_DELIVERY,
            error_code="cos_unavailable",
        )
        return error_response("cos_unavailable", status=503)

    available: list[dict[str, object]] = []
    unavailable_roles: list[str] = []
    temporary_failure = False
    for variant in variants:
        try:
            candidate = catalog.get_candidate(source_key=variant.source_key)
            if not candidate_matches_variant(candidate, variant):
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
        error_code = "cos_unavailable" if temporary_failure else "image_unavailable"
        record_operational_issue(
            actor=actor,
            assignment_id=assignment_id,
            kind=OperationalIssue.Kind.MEDIA_DELIVERY,
            error_code=error_code,
        )
        return error_response(error_code, status=503 if temporary_failure else 409)
    if unavailable_roles:
        record_operational_issue(
            actor=actor,
            assignment_id=assignment_id,
            kind=OperationalIssue.Kind.MEDIA_DELIVERY,
            error_code="cos_unavailable" if temporary_failure else "media_variant_unavailable",
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
        return variant_candidate(
            catalog.get_candidate(source_key=candidate.source_key), role=candidate.role
        )

    annotation_round: Task | None = None
    try:
        preview_id, expected_plan_sha256 = publication_request(request)
        with transaction.atomic():
            publication = publish_stored_preview(
                preview_id=preview_id,
                actor=actor,
                expected_plan_sha256=expected_plan_sha256,
                candidate_loader=load_current_candidate,
            )
            if publication.create_annotation_round:
                annotation_round = create_manual_annotation_round(
                    actor=actor,
                    source_preview_id=preview_id,
                    asset=publication.asset,
                    media_variants=publication.media_variants,
                )
    except ImportRequestError as error:
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

    return JsonResponse(
        {
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
                    "created": media_variant.media_variant_id
                    in publication.created_media_variant_ids,
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
        },
        status=201 if publication.created_asset else 200,
    )
