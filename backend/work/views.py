from __future__ import annotations

from contextlib import suppress
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpRequest, JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from identity.authorization import ResourceNotFound
from identity.http import (
    current_session_key,
    error_response,
    opaque_uuid,
    request_json,
    require_admin,
    require_worker,
)
from identity.models import User
from identity.services import WorkspaceLeaseLost, record_audit_event
from media.catalog import (
    CosCatalogUnavailable,
    MediaCandidateIntegrityConflict,
    MediaCandidateInvalid,
    MediaCandidateNotFound,
    candidate_matches_variant,
    get_cos_catalog,
)
from media.models import MediaVariant

from .annotation_state import AnnotationStateError
from .batch_exports import request_batch_export
from .jobs import REGISTERED_AUDIT_TYPES, analysis_job_metrics, request_revision_audit
from .manifests import ANNOTATION_INTERACTION_V1, ANNOTATION_VIEWER_V1
from .meta_schema import SUPPORTED_LOCALES, meta_contract_payload
from .metric_snapshots import request_metric_snapshot
from .models import (
    AnalysisJob,
    AnnotationRevision,
    Assignment,
    AuditArtifact,
    BatchExportSnapshot,
    BlockDisposition,
    BlockReport,
    CurrentDraft,
    GuidanceEvent,
    MetricSnapshot,
    PredictionArtifact,
    PredictionImportPreview,
    ReworkRequest,
    Task,
    WorkBatch,
)
from .operations import batch_operational_snapshot, batch_review_queue, record_operational_issue
from .prediction import preview_prediction_import, publish_prediction_import
from .services import (
    accept_owned_rework_request,
    acknowledge_owned_guidance_event,
    adjudicate_task,
    assign_task,
    block_owned_assignment,
    create_guidance_event,
    create_rework_request,
    create_scope_rework_flow,
    create_semi_task_from_prediction,
    create_work_batch,
    dispose_blocked_assignment,
    freeze_work_batch,
    get_or_create_owned_current_draft,
    get_owned_assignment,
    get_revision_for_admin,
    list_owned_guidance_events,
    list_owned_rework_requests,
    open_owned_assignment,
    reopen_work_batch,
    review_revision,
    rework_request_status,
    save_owned_current_draft,
    select_worker_revision_for_delivery,
    set_owned_assignment_queue_state,
    start_owned_revision_cycle,
    submit_owned_current_draft,
)


def assignment_payload(assignment: Assignment) -> dict[str, object]:
    return {
        "assignment_id": str(assignment.assignment_id),
        "batch_id": str(assignment.batch_id),
        "order_index": assignment.order_index,
        "queue_state": assignment.queue_state,
        "review_state": assignment.review_state,
        "task": {
            "active_time_rule_version": assignment.task.active_time_rule_version,
            "external_task_key": assignment.task.external_task_key,
            "meta_contract": meta_contract_payload(
                schema_version=assignment.task.meta_schema_version,
                copy_version=assignment.task.meta_copy_version,
                task_mode=assignment.task.mode,
            ),
            "mode": assignment.task.mode,
            "task_id": str(assignment.task_id),
        },
        "work_state": assignment.work_state,
    }


def work_batch_payload(batch: WorkBatch) -> dict[str, object]:
    return {"batch_id": str(batch.batch_id), "name": batch.name, "status": batch.status}


def admin_work_batch_payload(batch: WorkBatch) -> dict[str, object]:
    return {
        **work_batch_payload(batch),
        "consensus_policy": batch.consensus_policy,
        "policy_frozen_at": (
            None if batch.policy_frozen_at is None else batch.policy_frozen_at.isoformat()
        ),
        "scope_policy": batch.scope_policy,
    }


def rework_request_payload(rework: ReworkRequest) -> dict[str, object]:
    return {
        "assignment_id": str(rework.assignment_id),
        "due_at": rework.due_at.isoformat(),
        "instruction": rework.instruction,
        "request_id": str(rework.request_id),
        "status": rework_request_status(rework),
    }


def guidance_event_payload(guidance: GuidanceEvent) -> dict[str, object]:
    return {
        "acknowledged_at": (
            None if guidance.acknowledged_at is None else guidance.acknowledged_at.isoformat()
        ),
        "administrator_id": str(guidance.actor.worker_id),
        "assignment_id": str(guidance.assignment_id),
        "batch_id": str(guidance.assignment.batch_id),
        "category": guidance.category,
        "channel": guidance.channel,
        "created_at": guidance.created_at.isoformat(),
        "feedback_draft_cycle_id": (
            None
            if guidance.feedback_draft_cycle_id is None
            else str(guidance.feedback_draft_cycle_id)
        ),
        "guidance_id": str(guidance.guidance_id),
        "revision_id": (
            None if guidance.source_revision_id is None else str(guidance.source_revision_id)
        ),
        "summary": guidance.summary,
        "task_id": str(guidance.assignment.task_id),
        "worker_id": str(guidance.assignment.worker.worker_id),
    }


def current_draft_payload(draft: CurrentDraft) -> dict[str, object]:
    return {
        "draft_cycle_id": str(draft.draft_cycle_id),
        "draft_id": str(draft.draft_id),
        "draft_version": draft.draft_version,
        "state": draft.state,
        "state_sha": draft.state_sha,
        "updated_at": draft.updated_at.isoformat().replace("+00:00", "Z"),
    }


def prediction_preview_payload(
    preview: PredictionImportPreview,
    *,
    preview_media: dict[str, object],
) -> dict[str, object]:
    return {
        "asset_id": str(preview.asset_id),
        "expires_at": preview.expires_at.isoformat().replace("+00:00", "Z"),
        "importer_version": preview.importer_version,
        "media_variants": [
            {
                "media_variant_id": str(variant.media_variant_id),
                "role": variant.role,
            }
            for variant in preview.asset.media_variants.filter(published_at__isnull=False).order_by(
                "role", "media_variant_id"
            )
        ],
        "preview_id": str(preview.preview_id),
        "preview_media": preview_media,
        "raw_output_sha256": preview.raw_output_sha256,
        "state": preview.contract["state"],
        "state_sha256": preview.contract["state_sha256"],
    }


def prediction_artifact_payload(artifact: PredictionArtifact) -> dict[str, object]:
    return {
        "artifact_id": str(artifact.artifact_id),
        "artifact_sha256": artifact.artifact_sha256,
        "asset_id": str(artifact.asset_id),
        "created_at": artifact.created_at.isoformat().replace("+00:00", "Z"),
        "state_sha256": artifact.state_sha256,
    }


def _prediction_preview_media(preview: PredictionImportPreview) -> dict[str, object]:
    variants = preview.asset.media_variants.filter(published_at__isnull=False).order_by(
        "role", "created_at"
    )
    catalog = get_cos_catalog()
    for variant in sorted(
        variants,
        key=lambda item: item.role != MediaVariant.Role.COMPRESSED,
    ):
        try:
            candidate = catalog.get_candidate(source_key=variant.source_key)
        except (MediaCandidateInvalid, MediaCandidateNotFound):
            continue
        if not candidate_matches_variant(candidate, variant):
            raise MediaCandidateIntegrityConflict
        return {
            "coordinate_mapping": variant.coordinate_mapping,
            "height": variant.height,
            "media_variant_id": str(variant.media_variant_id),
            "role": variant.role,
            "url": candidate.preview_url,
            "width": variant.width,
        }
    raise MediaCandidateNotFound


def revision_submission_payload(revision: AnnotationRevision) -> dict[str, object]:
    payload: dict[str, object] = {
        "revision_id": str(revision.revision_id),
        "revision_no": revision.revision_no,
        "state_sha": revision.state_sha,
        "submitted_at": revision.submitted_at.isoformat().replace("+00:00", "Z"),
        "verification_status": (
            "assessment_complete"
            if revision.submission_assessments.exists()
            else "verification_pending"
        ),
    }
    for field in (
        "client_build_sha",
        "interaction_contract_version",
        "platform_release_id",
        "viewer_version",
    ):
        value = getattr(revision, field)
        if value is not None:
            payload[field] = value
    return payload


def audit_run_payload(job: AnalysisJob) -> dict[str, object]:
    artifact = AuditArtifact.objects.filter(run=job).first()
    return {
        "artifact": (
            None
            if artifact is None
            else {
                "artifact_id": str(artifact.artifact_id),
                "artifact_sha256": artifact.artifact_sha256,
                "audit_type": artifact.audit_type,
                "findings": artifact.findings,
                "input_sha256": artifact.input_sha256,
                "requires_review": artifact.requires_review,
            }
        ),
        "audit_type": job.kind.removeprefix("audit:"),
        "code_version": job.code_version,
        "input_sha256": job.input_sha256,
        "rule_version": job.rule_version,
        "run_id": str(job.job_id),
        "status": job.status,
    }


def block_report_payload(report: BlockReport) -> dict[str, object]:
    return {
        "block_id": str(report.block_id),
        "created_at": report.created_at.isoformat().replace("+00:00", "Z"),
        "draft_cycle_id": None if report.draft_cycle_id is None else str(report.draft_cycle_id),
        "reason_code": report.reason_code,
        "reason_schema_version": report.reason_schema_version,
        "reason_text": report.reason_text,
    }


def metric_snapshot_payload(
    snapshot: MetricSnapshot,
    *,
    include_input_manifest: bool,
    reused: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "code_version": snapshot.code_version,
        "created_at": snapshot.created_at.isoformat().replace("+00:00", "Z"),
        "cutoff": snapshot.cutoff.isoformat().replace("+00:00", "Z"),
        "input_count": len(snapshot.input_manifest.get("inputs", [])),
        "input_sha256": snapshot.input_sha256,
        "missing": snapshot.missing_count,
        "not_evaluable": snapshot.not_evaluable_count,
        "platform_release_id": snapshot.platform_release_id,
        "provisional": True,
        "result": snapshot.result_manifest,
        "reused_from_snapshot_id": str(snapshot.snapshot_id) if reused else None,
        "rule_version": snapshot.rule_version,
        "snapshot_id": str(snapshot.snapshot_id),
        "status": snapshot.status,
        "support": snapshot.support,
    }
    if include_input_manifest:
        payload["input_manifest"] = snapshot.input_manifest
    return payload


def batch_export_payload(
    snapshot: BatchExportSnapshot,
    *,
    include_manifest: bool,
    reused: bool = False,
) -> dict[str, object]:
    return {
        "code_version": snapshot.code_version,
        "created_at": snapshot.created_at.isoformat().replace("+00:00", "Z"),
        "cutoff": snapshot.cutoff.isoformat().replace("+00:00", "Z"),
        "export_id": str(snapshot.export_id),
        "export_sha256": snapshot.export_sha256 or None,
        "input_sha256": snapshot.input_sha256,
        "manifest": snapshot.export_manifest
        if include_manifest and snapshot.export_manifest
        else None,
        "platform_release_id": snapshot.platform_release_id,
        "reused_from_export_id": str(snapshot.export_id) if reused else None,
        "rule_version": snapshot.rule_version,
        "status": snapshot.status,
    }


def annotation_error_response(error: AnnotationStateError) -> JsonResponse:
    details: dict[str, object] = {
        "code": error.code,
        "field": error.field,
        "severity": "error",
    }
    for name in ("pair_id", "pair_index", "point_id", "portal_id"):
        value = getattr(error, name)
        if value is not None:
            details[name] = value
    return JsonResponse({"error": details}, status=400)


def _semi_prediction_failure_response(
    *,
    request: HttpRequest,
    actor: User,
    assignment_id: UUID,
    tab_id: UUID,
) -> JsonResponse:
    with suppress(ResourceNotFound, ValidationError, WorkspaceLeaseLost):
        block_owned_assignment(
            actor=actor,
            assignment_id=assignment_id,
            reason_code=BlockReport.ReasonCode.TECHNICAL_FAILURE,
            reason_text="semi_prediction_unavailable",
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    return error_response("semi_prediction_unavailable", status=409)


@require_http_methods(["GET", "POST"])
def admin_work_batches_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    if request.method == "GET":
        return JsonResponse(
            {"batches": [admin_work_batch_payload(batch) for batch in WorkBatch.objects.all()]}
        )
    payload = request_json(request)
    allowed_fields = {"consensus_policy", "name", "scope_policy"}
    if (
        payload is None
        or "name" not in payload
        or not set(payload) <= allowed_fields
        or not isinstance(payload.get("name"), str)
    ):
        return error_response("invalid_work_batch", status=400)
    try:
        batch = create_work_batch(
            consensus_policy=payload.get("consensus_policy"),
            name=payload["name"],
            scope_policy=payload.get("scope_policy"),
        )
    except ValidationError as error:
        return error_response(error.code or "invalid_work_batch", status=400)
    return JsonResponse(admin_work_batch_payload(batch), status=201)


@require_GET
def admin_analysis_job_metrics_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    return JsonResponse(analysis_job_metrics())


@require_GET
def admin_batch_operations_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        snapshot = batch_operational_snapshot(batch_id=batch_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    latest_snapshot = MetricSnapshot.objects.filter(batch_id=batch_id).first()
    snapshot["metric_snapshot"] = (
        None
        if latest_snapshot is None
        else metric_snapshot_payload(latest_snapshot, include_input_manifest=False)
    )
    return JsonResponse(snapshot)


@require_GET
def admin_batch_review_queue_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        items = batch_review_queue(
            batch_id=batch_id,
            reason_code=request.GET.get("reason_code"),
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    return JsonResponse({"batch_id": str(batch_id), "items": items})


@require_POST
def admin_batch_metric_snapshots_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    if request_json(request) != {}:
        return error_response("invalid_metric_snapshot_request", status=400)
    try:
        snapshot, created, reused = request_metric_snapshot(batch_id=batch_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    record_audit_event(
        actor=actor,
        target_type="metric_snapshot",
        target_id=snapshot.snapshot_id,
        action="metric_snapshot.requested",
        reason="reused_existing_input" if reused else "administrator_request",
        details={"batch_id": str(batch_id), "input_sha256": snapshot.input_sha256},
    )
    return JsonResponse(
        metric_snapshot_payload(
            snapshot,
            include_input_manifest=True,
            reused=reused,
        ),
        status=200 if reused else 202,
    )


@require_POST
def admin_batch_exports_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    if request_json(request) != {}:
        return error_response("invalid_batch_export_request", status=400)
    try:
        snapshot, _created, reused = request_batch_export(batch_id=batch_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    record_audit_event(
        actor=actor,
        target_type="batch_export_snapshot",
        target_id=snapshot.export_id,
        action="batch_export.requested",
        reason="reused_existing_input" if reused else "administrator_request",
        details={"batch_id": str(batch_id), "input_sha256": snapshot.input_sha256},
    )
    return JsonResponse(
        batch_export_payload(snapshot, include_manifest=reused, reused=reused),
        status=200 if reused else 202,
    )


@require_GET
def admin_batch_export_view(request: HttpRequest, export_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    snapshot = BatchExportSnapshot.objects.filter(export_id=export_id).first()
    if snapshot is None:
        return error_response(ResourceNotFound.code, status=404)
    return JsonResponse(batch_export_payload(snapshot, include_manifest=True))


@require_POST
def admin_prediction_preview_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    expected = {
        "asset_id",
        "checkpoint_sha256",
        "import_source",
        "importer_version",
        "layout_text",
        "model_name",
        "model_version",
    }
    asset_id = None if payload is None else opaque_uuid(payload.get("asset_id"))
    if (
        payload is None
        or set(payload) != expected
        or asset_id is None
        or any(not isinstance(payload.get(field), str) for field in expected - {"asset_id"})
    ):
        return error_response("invalid_prediction_import", status=400)
    try:
        preview = preview_prediction_import(
            actor=actor,
            asset_id=asset_id,
            importer_version=payload["importer_version"],
            layout_text=payload["layout_text"],
            model_name=payload["model_name"],
            model_version=payload["model_version"],
            checkpoint_sha256=payload["checkpoint_sha256"],
            import_source=payload["import_source"],
        )
        media = _prediction_preview_media(preview)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "invalid_prediction_import", status=400)
    except CosCatalogUnavailable:
        return error_response("cos_unavailable", status=503)
    except MediaCandidateIntegrityConflict:
        return error_response("media_candidate_integrity_conflict", status=409)
    except MediaCandidateNotFound:
        return error_response("image_unavailable", status=409)
    return JsonResponse(prediction_preview_payload(preview, preview_media=media), status=201)


@require_POST
def admin_prediction_publish_view(request: HttpRequest, preview_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if payload != {}:
        return error_response("invalid_prediction_publication", status=400)
    try:
        artifact, created = publish_prediction_import(actor=actor, preview_id=preview_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "prediction_preview_conflict", status=409)
    return JsonResponse(prediction_artifact_payload(artifact), status=201 if created else 200)


@require_POST
def admin_prediction_semi_tasks_view(request: HttpRequest, artifact_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    raw_variant_ids = None if payload is None else payload.get("media_variant_ids")
    if (
        payload is None
        or set(payload) != {"media_variant_ids"}
        or not isinstance(raw_variant_ids, list)
        or not raw_variant_ids
        or any(not isinstance(value, str) for value in raw_variant_ids)
    ):
        return error_response("invalid_semi_task_request", status=400)
    media_variant_ids = [opaque_uuid(value) for value in raw_variant_ids]
    if any(value is None for value in media_variant_ids) or len(set(media_variant_ids)) != len(
        media_variant_ids
    ):
        return error_response("invalid_semi_task_request", status=400)
    try:
        task, created = create_semi_task_from_prediction(
            actor=actor,
            prediction_artifact_id=artifact_id,
            media_variant_ids=[value for value in media_variant_ids if value is not None],
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "semi_task_conflict", status=409)
    return JsonResponse(
        {
            "mode": task.mode,
            "reused": not created,
            "status": task.status,
            "task_id": str(task.task_id),
        },
        status=201 if created else 200,
    )


@require_POST
def admin_batch_freeze_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        batch = freeze_work_batch(batch_id=batch_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "batch_transition_invalid", status=409)
    record_audit_event(
        actor=actor,
        target_type="work_batch",
        target_id=batch.batch_id,
        action="batch.frozen",
        reason="administrator_request",
    )
    return JsonResponse(work_batch_payload(batch))


@require_POST
def admin_batch_reopen_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        batch = reopen_work_batch(batch_id=batch_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "batch_transition_invalid", status=409)
    record_audit_event(
        actor=actor,
        target_type="work_batch",
        target_id=batch.batch_id,
        action="batch.reopened",
        reason="administrator_request",
    )
    return JsonResponse(work_batch_payload(batch))


@require_POST
def admin_batch_assignments_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if payload is None or set(payload) != {"task_id", "worker_id"}:
        return error_response("invalid_assignment", status=400)
    task_id = opaque_uuid(payload.get("task_id"))
    worker_id = opaque_uuid(payload.get("worker_id"))
    if task_id is None or worker_id is None:
        return error_response("invalid_assignment", status=400)
    batch = WorkBatch.objects.filter(batch_id=batch_id, status=WorkBatch.Status.OPEN).first()
    task = Task.objects.filter(
        task_id=task_id,
        status=Task.Status.PUBLISHED,
    ).first()
    worker = User.objects.filter(
        worker_id=worker_id,
        role=User.Role.WORKER,
        is_active=True,
    ).first()
    if batch is None or task is None or worker is None:
        return error_response("resource_not_found", status=404)
    try:
        assignment = assign_task(batch=batch, task=task, worker=worker)
    except (IntegrityError, ValidationError):
        return error_response("assignment_conflict", status=409)
    record_audit_event(
        actor=actor,
        target_worker=worker,
        target_type="assignment",
        target_id=assignment.assignment_id,
        action="assignment.created",
        reason="administrator_request",
        details={
            "assignment_id": str(assignment.assignment_id),
            "batch_id": str(batch.batch_id),
            "task_id": str(task.task_id),
        },
    )
    return JsonResponse(assignment_payload(assignment), status=201)


@require_GET
def worker_batches_view(request: HttpRequest) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    batches = WorkBatch.objects.filter(assignments__worker=actor).distinct()
    return JsonResponse(
        {
            "batches": [
                {
                    "batch_id": str(batch.batch_id),
                    "name": batch.name,
                    "status": batch.status,
                    "worker_complete": not Assignment.objects.filter(
                        batch=batch,
                        worker=actor,
                        work_state__in=(
                            Assignment.WorkState.ASSIGNED,
                            Assignment.WorkState.IN_PROGRESS,
                        ),
                    ).exists(),
                }
                for batch in batches
            ]
        }
    )


@require_GET
def worker_batch_assignments_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    assignments = list(
        Assignment.objects.select_related("batch", "task")
        .filter(batch_id=batch_id, worker=actor)
        .order_by("order_index")
    )
    if not assignments:
        return error_response("resource_not_found", status=404)
    return JsonResponse({"assignments": [assignment_payload(item) for item in assignments]})


@require_GET
def worker_assignment_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        assignment = get_owned_assignment(actor=actor, assignment_id=assignment_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    return JsonResponse(assignment_payload(assignment))


def _write_payload(request: HttpRequest) -> tuple[dict[str, object] | None, UUID | None]:
    payload = request_json(request)
    if payload is None:
        return None, None
    return payload, opaque_uuid(payload.get("tab_id"))


@require_POST
def worker_assignment_open_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload, tab_id = _write_payload(request)
    if payload is None or set(payload) != {"tab_id"} or tab_id is None:
        return error_response("invalid_assignment_open", status=400)
    try:
        assignment = open_owned_assignment(
            actor=actor,
            assignment_id=assignment_id,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        if request.method == "PUT":
            record_operational_issue(
                actor=actor,
                assignment_id=assignment_id,
                kind="draft_save",
                error_code="workspace_lease_lost",
            )
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "assignment_not_editable", status=409)
    return JsonResponse(assignment_payload(assignment))


@require_POST
def worker_assignment_queue_state_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload, tab_id = _write_payload(request)
    if payload is None or set(payload) != {"queue_state", "tab_id"} or tab_id is None:
        return error_response("invalid_queue_state_request", status=400)
    queue_state = payload.get("queue_state")
    if not isinstance(queue_state, str):
        return error_response("queue_state_invalid", status=400)
    try:
        assignment = set_owned_assignment_queue_state(
            actor=actor,
            assignment_id=assignment_id,
            queue_state=queue_state,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        status = 400 if error.code == "queue_state_invalid" else 409
        return error_response(error.code or "queue_state_invalid", status=status)
    return JsonResponse(assignment_payload(assignment))


@require_http_methods(["GET", "PUT"])
def worker_assignment_draft_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    expected_version: int | None = None
    draft_state: object = None
    if request.method == "GET":
        tab_id = opaque_uuid(request.GET.get("tab_id"))
        if tab_id is None:
            return error_response("invalid_draft_request", status=400)
    else:
        payload, tab_id = _write_payload(request)
        if payload is None or set(payload) != {"expected_draft_version", "state", "tab_id"}:
            return error_response("invalid_draft_request", status=400)
        raw_expected_version = payload.get("expected_draft_version")
        if (
            tab_id is None
            or isinstance(raw_expected_version, bool)
            or not isinstance(raw_expected_version, int)
            or raw_expected_version < 0
        ):
            return error_response("invalid_draft_request", status=400)
        expected_version = raw_expected_version
        draft_state = payload.get("state")
    try:
        if request.method == "GET":
            draft = get_or_create_owned_current_draft(
                actor=actor,
                assignment_id=assignment_id,
                session_key=current_session_key(request),
                session_token=request.session.get("active_workspace_token"),
                tab_id=tab_id,
            )
        else:
            if expected_version is None:
                return error_response("invalid_draft_request", status=400)
            draft = save_owned_current_draft(
                actor=actor,
                assignment_id=assignment_id,
                session_key=current_session_key(request),
                session_token=request.session.get("active_workspace_token"),
                tab_id=tab_id,
                expected_draft_version=expected_version,
                state=draft_state,
            )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except AnnotationStateError as error:
        if request.method == "PUT":
            record_operational_issue(
                actor=actor,
                assignment_id=assignment_id,
                kind="annotation_structure",
                error_code=error.code,
            )
        return annotation_error_response(error)
    except ValidationError as error:
        if request.method == "PUT":
            record_operational_issue(
                actor=actor,
                assignment_id=assignment_id,
                kind="draft_save",
                error_code=error.code or "draft_conflict",
            )
        if error.code == "semi_prediction_unavailable":
            return _semi_prediction_failure_response(
                request=request,
                actor=actor,
                assignment_id=assignment_id,
                tab_id=tab_id,
            )
        if error.code == "draft_conflict":
            current = (
                CurrentDraft.objects.filter(
                    draft_cycle__assignment_id=assignment_id,
                    draft_cycle__closed_at__isnull=True,
                )
                .only("draft_version", "updated_at")
                .first()
            )
            if current is not None:
                return JsonResponse(
                    {
                        "error": {
                            "code": "draft_conflict",
                            "server_draft_version": current.draft_version,
                            "server_updated_at": current.updated_at.isoformat().replace(
                                "+00:00", "Z"
                            ),
                        }
                    },
                    status=409,
                )
        return error_response(error.code or "draft_conflict", status=409)
    return JsonResponse(current_draft_payload(draft))


@require_POST
def worker_assignment_submit_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload, tab_id = _write_payload(request)
    if payload is None or set(payload) != {
        "client_build_sha",
        "expected_state_sha",
        "idempotency_key",
        "interaction_contract_version",
        "locale",
        "tab_id",
        "viewer_version",
    }:
        return error_response("invalid_submission", status=400)
    state_sha = payload.get("expected_state_sha")
    idempotency_key = opaque_uuid(payload.get("idempotency_key"))
    locale = payload.get("locale")
    client_build_sha = payload.get("client_build_sha")
    viewer_version = payload.get("viewer_version")
    interaction_contract_version = payload.get("interaction_contract_version")
    if (
        tab_id is None
        or not isinstance(state_sha, str)
        or len(state_sha) != 64
        or any(character not in "0123456789abcdef" for character in state_sha)
        or idempotency_key is None
        or not isinstance(locale, str)
        or locale not in SUPPORTED_LOCALES
        or not isinstance(client_build_sha, str)
        or not 1 <= len(client_build_sha) <= 64
        or viewer_version != ANNOTATION_VIEWER_V1
        or interaction_contract_version != ANNOTATION_INTERACTION_V1
    ):
        return error_response("invalid_submission", status=400)
    try:
        revision, created = submit_owned_current_draft(
            actor=actor,
            assignment_id=assignment_id,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
            expected_state_sha=state_sha,
            idempotency_key=idempotency_key,
            locale=locale,
            client_build_sha=client_build_sha,
            viewer_version=viewer_version,
            interaction_contract_version=interaction_contract_version,
        )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except AnnotationStateError as error:
        return annotation_error_response(error)
    except ValidationError as error:
        if error.code == "semi_prediction_unavailable":
            return _semi_prediction_failure_response(
                request=request,
                actor=actor,
                assignment_id=assignment_id,
                tab_id=tab_id,
            )
        return error_response(error.code or "submission_conflict", status=409)
    return JsonResponse(revision_submission_payload(revision), status=201 if created else 200)


@require_POST
def worker_assignment_assist_candidate_view(
    request: HttpRequest, assignment_id: UUID
) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        assignment = get_owned_assignment(actor=actor, assignment_id=assignment_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    if not assignment.task.assist_enabled:
        return error_response("assist_disabled", status=403)
    return error_response("assist_engine_unavailable", status=503)


@require_POST
def worker_assignment_revise_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload, tab_id = _write_payload(request)
    if payload is None or set(payload) != {"tab_id"} or tab_id is None:
        return error_response("invalid_revision_cycle", status=400)
    try:
        assignment = start_owned_revision_cycle(
            actor=actor,
            assignment_id=assignment_id,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "revision_cycle_conflict", status=409)
    return JsonResponse(assignment_payload(assignment), status=201)


@require_POST
def worker_assignment_block_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if payload is None or set(payload) != {"reason_code", "reason_text", "tab_id"}:
        return error_response("invalid_assignment_block", status=400)
    tab_id = opaque_uuid(payload.get("tab_id"))
    reason_code = payload.get("reason_code")
    reason_text = payload.get("reason_text")
    if tab_id is None or not isinstance(reason_code, str) or not isinstance(reason_text, str):
        return error_response("invalid_assignment_block", status=400)
    try:
        assignment, report = block_owned_assignment(
            actor=actor,
            assignment_id=assignment_id,
            reason_code=reason_code,
            reason_text=reason_text,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        code = error.code or "assignment_block_conflict"
        status = 400 if code.startswith("block_reason_") else 409
        return error_response(code, status=status)
    return JsonResponse(
        {**assignment_payload(assignment), "block_report": block_report_payload(report)}, status=201
    )


@require_POST
def admin_block_disposition_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if payload is None:
        return error_response("invalid_block_disposition", status=400)
    action = payload.get("action")
    reason = payload.get("reason")
    expected_fields = (
        {"action", "reason", "worker_id"} if action == "reassign" else {"action", "reason"}
    )
    if (
        set(payload) != expected_fields
        or not isinstance(action, str)
        or not isinstance(reason, str)
    ):
        return error_response("invalid_block_disposition", status=400)
    replacement_worker = None
    if action == BlockDisposition.Action.REASSIGN:
        worker_id = opaque_uuid(payload.get("worker_id"))
        if worker_id is None:
            return error_response("invalid_block_disposition", status=400)
        replacement_worker = User.objects.filter(worker_id=worker_id).first()
    try:
        assignment, disposition = dispose_blocked_assignment(
            actor=actor,
            assignment_id=assignment_id,
            action=action,
            reason=reason,
            replacement_worker=replacement_worker,
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except (IntegrityError, ValidationError) as error:
        code = getattr(error, "code", None) or "block_disposition_conflict"
        status = (
            400 if code.startswith("block_") and code.endswith(("invalid", "required")) else 409
        )
        return error_response(code, status=status)
    return JsonResponse(
        {
            **assignment_payload(assignment),
            "action": disposition.action,
            "disposition_id": str(disposition.disposition_id),
            "replacement_assignment_id": (
                None
                if disposition.replacement_assignment_id is None
                else str(disposition.replacement_assignment_id)
            ),
        },
        status=201,
    )


@require_GET
def admin_revision_view(request: HttpRequest, revision_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        revision = get_revision_for_admin(actor=actor, revision_id=revision_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    return JsonResponse(
        {
            **revision_submission_payload(revision),
            "assignment_id": str(revision.assignment_id),
            "copy_version": revision.meta_copy_version,
            "feedback_exposed": (
                revision.draft_cycle.guidance_events.exists()
                or ReworkRequest.objects.filter(completed_revision=revision).exists()
            ),
            "locale": revision.submission_locale,
            "schema_version": revision.meta_schema_version,
            "state": revision.state,
            "worker_id": str(revision.worker.worker_id),
        }
    )


@require_POST
def admin_revision_audit_runs_view(request: HttpRequest, revision_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if (
        payload is None
        or set(payload) != {"audit_type"}
        or payload.get("audit_type") not in REGISTERED_AUDIT_TYPES
    ):
        return error_response("audit_type_unregistered", status=400)
    try:
        job, created = request_revision_audit(
            revision_id=revision_id,
            audit_type=payload["audit_type"],
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValueError as error:
        return error_response(str(error), status=409)
    record_audit_event(
        actor=actor,
        target_type="audit_run",
        target_id=job.job_id,
        action="audit_run.requested",
        reason="administrator_request" if created else "reused_existing_input",
        details={
            "audit_type": payload["audit_type"],
            "input_sha256": job.input_sha256,
            "revision_id": str(revision_id),
        },
    )
    return JsonResponse(
        audit_run_payload(job),
        status=202 if job.status != AnalysisJob.Status.SUCCEEDED else 200,
    )


@require_GET
def admin_audit_run_view(request: HttpRequest, run_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    job = AnalysisJob.objects.filter(job_id=run_id, kind__startswith="audit:").first()
    if job is None:
        return error_response(ResourceNotFound.code, status=404)
    return JsonResponse(audit_run_payload(job))


@require_POST
def admin_revision_review_view(request: HttpRequest, revision_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if (
        payload is None
        or set(payload) != {"outcome", "reason"}
        or not isinstance(payload.get("outcome"), str)
        or not isinstance(payload.get("reason"), str)
    ):
        return error_response("invalid_review", status=400)
    try:
        review = review_revision(
            actor=actor,
            revision_id=revision_id,
            outcome=payload["outcome"],
            reason=payload["reason"],
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "invalid_review", status=400)
    return JsonResponse(
        {
            "outcome": review.outcome,
            "review_id": str(review.review_id),
            "revision_id": str(review.revision_id),
            "supersedes_review_id": (
                None if review.supersedes_id is None else str(review.supersedes_id)
            ),
        },
        status=201,
    )


@require_POST
def admin_task_adjudications_view(request: HttpRequest, task_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    raw_source_ids = None if payload is None else payload.get("source_revision_ids")
    if (
        payload is None
        or set(payload) != {"reason", "source_revision_ids", "state"}
        or not isinstance(payload.get("reason"), str)
        or not isinstance(raw_source_ids, list)
        or any(not isinstance(value, str) for value in raw_source_ids)
    ):
        return error_response("invalid_adjudication", status=400)
    source_ids = [opaque_uuid(value) for value in raw_source_ids]
    if any(value is None for value in source_ids):
        return error_response("invalid_adjudication", status=400)
    try:
        adjudication, selection = adjudicate_task(
            actor=actor,
            task_id=task_id,
            state=payload.get("state"),
            source_revision_ids=[value for value in source_ids if value is not None],
            reason=payload["reason"],
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except AnnotationStateError as error:
        return annotation_error_response(error)
    except ValidationError as error:
        code = error.code or "adjudication_conflict"
        status = 400 if code.startswith("adjudication_reason_") else 409
        return error_response(code, status=status)
    return JsonResponse(
        {
            "adjudication_id": str(adjudication.adjudication_id),
            "selection_id": str(selection.selection_id),
            "state_sha": adjudication.state_sha,
            "task_id": str(adjudication.task_id),
        },
        status=201,
    )


@require_POST
def admin_task_delivery_selection_view(request: HttpRequest, task_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if (
        payload is None
        or set(payload) != {"reason", "worker_revision_id"}
        or not isinstance(payload.get("reason"), str)
    ):
        return error_response("invalid_delivery_selection", status=400)
    worker_revision_id = opaque_uuid(payload.get("worker_revision_id"))
    if worker_revision_id is None:
        return error_response("invalid_delivery_selection", status=400)
    try:
        selection = select_worker_revision_for_delivery(
            actor=actor,
            task_id=task_id,
            worker_revision_id=worker_revision_id,
            reason=payload["reason"],
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        code = error.code or "delivery_selection_conflict"
        status = 400 if code.startswith("delivery_selection_reason_") else 409
        return error_response(code, status=status)
    return JsonResponse(
        {
            "selection_id": str(selection.selection_id),
            "task_id": str(selection.task_id),
            "worker_revision_id": str(selection.worker_revision_id),
        },
        status=201,
    )


@require_GET
def worker_rework_requests_view(request: HttpRequest) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        reworks = list_owned_rework_requests(actor=actor)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    return JsonResponse({"requests": [rework_request_payload(item) for item in reworks]})


@require_GET
def worker_guidance_events_view(request: HttpRequest) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    try:
        events = list_owned_guidance_events(actor=actor)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    return JsonResponse({"events": [guidance_event_payload(item) for item in events]})


@require_POST
def worker_guidance_event_acknowledge_view(request: HttpRequest, guidance_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if payload != {}:
        return error_response("invalid_guidance_acknowledgement", status=400)
    try:
        guidance = acknowledge_owned_guidance_event(actor=actor, guidance_id=guidance_id)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    return JsonResponse(guidance_event_payload(guidance))


@require_POST
def admin_assignment_guidance_events_view(
    request: HttpRequest, assignment_id: UUID
) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if (
        payload is None
        or set(payload) != {"category", "channel", "revision_id", "summary"}
        or not isinstance(payload.get("category"), str)
        or not isinstance(payload.get("channel"), str)
        or not isinstance(payload.get("summary"), str)
    ):
        return error_response("invalid_guidance_event", status=400)
    raw_revision_id = payload.get("revision_id")
    revision_id = None if raw_revision_id is None else opaque_uuid(raw_revision_id)
    if raw_revision_id is not None and revision_id is None:
        return error_response("invalid_guidance_event", status=400)
    try:
        guidance = create_guidance_event(
            actor=actor,
            assignment_id=assignment_id,
            category=payload["category"],
            channel=payload["channel"],
            source_revision_id=revision_id,
            summary=payload["summary"],
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "invalid_guidance_event", status=400)
    return JsonResponse(guidance_event_payload(guidance), status=201)


@require_POST
def worker_rework_request_accept_view(request: HttpRequest, request_id: UUID) -> JsonResponse:
    actor = require_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload, tab_id = _write_payload(request)
    if payload is None or set(payload) != {"tab_id"} or tab_id is None:
        return error_response("invalid_rework_request", status=400)
    try:
        rework = accept_owned_rework_request(
            actor=actor,
            request_id=request_id,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        return error_response(error.code or "rework_request_conflict", status=409)
    return JsonResponse(rework_request_payload(rework), status=201)


@require_POST
def admin_revision_rework_request_view(request: HttpRequest, revision_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if (
        payload is None
        or set(payload) != {"adjudication_id", "due_at", "instruction"}
        or not isinstance(payload.get("due_at"), str)
        or not isinstance(payload.get("instruction"), str)
    ):
        return error_response("invalid_rework_request", status=400)
    adjudication_id = opaque_uuid(payload.get("adjudication_id"))
    due_at = parse_datetime(payload["due_at"])
    if adjudication_id is None or due_at is None:
        return error_response("invalid_rework_request", status=400)
    try:
        rework = create_rework_request(
            actor=actor,
            initial_revision_id=revision_id,
            source_adjudication_id=adjudication_id,
            instruction=payload["instruction"],
            due_at=due_at,
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        code = error.code or "rework_request_conflict"
        status = 400 if code in {"rework_due_invalid", "rework_instruction_required"} else 409
        return error_response(code, status=status)
    return JsonResponse(rework_request_payload(rework), status=201)


@require_POST
def admin_revision_scope_rework_view(request: HttpRequest, revision_id: UUID) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if (
        payload is None
        or set(payload) != {"due_at", "instruction", "reason"}
        or not isinstance(payload.get("due_at"), str)
        or not isinstance(payload.get("instruction"), str)
        or not isinstance(payload.get("reason"), str)
    ):
        return error_response("invalid_scope_rework", status=400)
    due_at = parse_datetime(payload["due_at"])
    if due_at is None:
        return error_response("invalid_scope_rework", status=400)
    try:
        review, adjudication, rework = create_scope_rework_flow(
            actor=actor,
            initial_revision_id=revision_id,
            reason=payload["reason"],
            instruction=payload["instruction"],
            due_at=due_at,
        )
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except AnnotationStateError as error:
        return annotation_error_response(error)
    except ValidationError as error:
        code = error.code or "scope_rework_conflict"
        status = (
            400
            if code == "rework_due_invalid"
            or code.startswith(("adjudication_reason_", "review_reason_", "rework_instruction_"))
            else 409
        )
        return error_response(code, status=status)
    return JsonResponse(
        {
            **rework_request_payload(rework),
            "adjudication_id": str(adjudication.adjudication_id),
            "review_id": str(review.review_id),
        },
        status=201,
    )
