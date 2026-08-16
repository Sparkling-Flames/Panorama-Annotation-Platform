from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from identity.authorization import ResourceNotFound
from identity.models import User
from identity.services import lock_worker_workspace_for_write, record_audit_event
from media.models import Asset, MediaImportPreview, MediaVariant

from .annotation_state import (
    annotation_state_sha,
    canonicalize_annotation_state,
    empty_annotation_state,
    validate_annotation_submission,
)
from .jobs import enqueue_submission_assessment
from .manifests import PLATFORM_RELEASE_ID_V1
from .meta_schema import META_COPY_V1, META_SCHEMA_V1, SUPPORTED_LOCALES
from .models import (
    AdjudicatedRevision,
    AnnotationRevision,
    Assignment,
    BlockDisposition,
    BlockReport,
    CurrentDraft,
    DraftCycle,
    GuidanceEvent,
    PredictionArtifact,
    ReviewRecord,
    ReworkRequest,
    Task,
    TaskDeliverySelection,
    TaskMediaVariant,
    WorkBatch,
)
from .prediction import prediction_artifact_matches_contract
from .scope_aggregation import (
    default_consensus_policy,
    default_scope_policy,
    validate_consensus_policy,
    validate_scope_policy,
)

BLOCK_REASON_SCHEMA_VERSION = "assignment-block-v1"
REVIEW_RULE_VERSION = "revision-review-v1"
ADJUDICATION_RULE_VERSION = "adjudication-v1"


def create_work_batch(
    *,
    name: str,
    consensus_policy: object | None = None,
    scope_policy: object | None = None,
) -> WorkBatch:
    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("A batch name is required.", code="batch_name_required")
    return WorkBatch.objects.create(
        consensus_policy=validate_consensus_policy(
            default_consensus_policy() if consensus_policy is None else consensus_policy
        ),
        name=clean_name,
        scope_policy=validate_scope_policy(
            default_scope_policy() if scope_policy is None else scope_policy
        ),
    )


@transaction.atomic
def freeze_work_batch(*, batch_id: UUID) -> WorkBatch:
    batch = WorkBatch.objects.select_for_update().filter(batch_id=batch_id).first()
    if batch is None:
        raise ResourceNotFound
    if batch.status == WorkBatch.Status.CLOSED:
        raise ValidationError("A closed batch cannot be frozen.", code="batch_transition_invalid")
    if batch.status == WorkBatch.Status.OPEN:
        batch.status = WorkBatch.Status.FROZEN
        if batch.policy_frozen_at is None:
            batch.policy_frozen_at = timezone.now()
        batch.save(update_fields=["policy_frozen_at", "status", "updated_at"])
    return batch


@transaction.atomic
def reopen_work_batch(*, batch_id: UUID) -> WorkBatch:
    batch = WorkBatch.objects.select_for_update().filter(batch_id=batch_id).first()
    if batch is None:
        raise ResourceNotFound
    if batch.status == WorkBatch.Status.CLOSED:
        raise ValidationError("A closed batch cannot be reopened.", code="batch_transition_invalid")
    if batch.status == WorkBatch.Status.FROZEN:
        batch.status = WorkBatch.Status.OPEN
        batch.save(update_fields=["status", "updated_at"])
    return batch


@transaction.atomic
def assign_task(*, batch: WorkBatch, task: Task, worker: User) -> Assignment:
    batch = WorkBatch.objects.select_for_update().get(batch_id=batch.batch_id)
    if batch.status != WorkBatch.Status.OPEN:
        raise ValidationError("The batch is not open.", code="assignment_batch_unavailable")
    task = Task.objects.select_for_update().get(task_id=task.task_id)
    if task.status != Task.Status.PUBLISHED:
        raise ValidationError("The task is not published.", code="assignment_task_unavailable")
    maximum_order = Assignment.objects.filter(batch=batch, worker=worker).aggregate(
        maximum=Max("order_index")
    )["maximum"]
    assignment = Assignment(
        batch=batch,
        task=task,
        worker=worker,
        order_index=0 if maximum_order is None else maximum_order + 1,
    )
    assignment.save()
    return assignment


def get_owned_assignment(*, actor: User, assignment_id: UUID) -> Assignment:
    if not actor.is_active or actor.role != User.Role.WORKER:
        raise ResourceNotFound
    assignment = (
        Assignment.objects.select_related("batch", "task", "worker")
        .filter(assignment_id=assignment_id, worker=actor)
        .first()
    )
    if assignment is None:
        raise ResourceNotFound
    return assignment


def _lock_owned_assignment(*, actor: User, assignment_id: UUID) -> Assignment:
    batch_id = (
        Assignment.objects.filter(assignment_id=assignment_id, worker=actor)
        .values_list("batch_id", flat=True)
        .first()
    )
    if batch_id is None:
        raise ResourceNotFound
    batch = WorkBatch.objects.select_for_update().filter(batch_id=batch_id).first()
    if batch is None:
        raise ResourceNotFound
    assignment = (
        Assignment.objects.select_for_update(of=("self",))
        .select_related("task", "worker")
        .filter(assignment_id=assignment_id, worker=actor)
        .first()
    )
    if assignment is None:
        raise ResourceNotFound
    assignment.batch = batch
    return assignment


def _require_available_assignment(assignment: Assignment) -> None:
    if assignment.batch.status != WorkBatch.Status.OPEN:
        raise ValidationError("The batch is not open.", code="batch_not_open")
    if assignment.task.status != Task.Status.PUBLISHED:
        raise ValidationError("The task is not published.", code="assignment_task_unavailable")


def lock_owned_writable_assignment(*, actor: User, assignment_id: UUID) -> Assignment:
    assignment = _lock_owned_assignment(actor=actor, assignment_id=assignment_id)
    _require_available_assignment(assignment)
    return assignment


@transaction.atomic
def open_owned_assignment(
    *,
    actor: User,
    assignment_id: UUID,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> Assignment:
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment = lock_owned_writable_assignment(actor=actor, assignment_id=assignment_id)
    if assignment.work_state == Assignment.WorkState.ASSIGNED:
        assignment.work_state = Assignment.WorkState.IN_PROGRESS
        update_fields = ["updated_at", "work_state"]
    elif assignment.work_state != Assignment.WorkState.IN_PROGRESS:
        raise ValidationError("The assignment cannot be opened.", code="assignment_not_editable")
    else:
        update_fields = []
    if assignment.queue_state == Assignment.QueueState.DEFERRED:
        assignment.queue_state = Assignment.QueueState.READY
        update_fields.append("queue_state")
    if update_fields:
        assignment.save(update_fields=update_fields)
    return assignment


@transaction.atomic
def set_owned_assignment_queue_state(
    *,
    actor: User,
    assignment_id: UUID,
    queue_state: str,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> Assignment:
    if queue_state not in Assignment.QueueState.values:
        raise ValidationError("Unsupported queue state.", code="queue_state_invalid")
    if queue_state == Assignment.QueueState.NEEDS_REVISIT:
        raise ValidationError(
            "Only review or administrator actions may require a revisit.",
            code="queue_state_forbidden",
        )
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment = lock_owned_writable_assignment(actor=actor, assignment_id=assignment_id)
    if assignment.work_state == Assignment.WorkState.REVOKED:
        raise ValidationError("The assignment is revoked.", code="assignment_revoked")
    if assignment.work_state not in {
        Assignment.WorkState.ASSIGNED,
        Assignment.WorkState.IN_PROGRESS,
    }:
        raise ValidationError("The assignment is not editable.", code="assignment_not_editable")
    if assignment.queue_state != queue_state:
        assignment.queue_state = queue_state
        assignment.save(update_fields=["queue_state", "updated_at"])
    return assignment


@transaction.atomic
def block_owned_assignment(
    *,
    actor: User,
    assignment_id: UUID,
    reason_code: str,
    reason_text: str,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> tuple[Assignment, BlockReport]:
    clean_text = reason_text.strip()
    if reason_code not in BlockReport.ReasonCode.values:
        raise ValidationError("Unsupported block reason.", code="block_reason_invalid")
    if (
        reason_code
        in {
            BlockReport.ReasonCode.TECHNICAL_FAILURE,
            BlockReport.ReasonCode.OTHER,
        }
        and not clean_text
    ):
        raise ValidationError(
            "This block reason requires an explanation.", code="block_reason_text_required"
        )
    if len(clean_text) > 2_000:
        raise ValidationError(
            "The block explanation is too long.", code="block_reason_text_too_long"
        )
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment = lock_owned_writable_assignment(actor=actor, assignment_id=assignment_id)
    if assignment.work_state not in {
        Assignment.WorkState.ASSIGNED,
        Assignment.WorkState.IN_PROGRESS,
    }:
        raise ValidationError("The assignment cannot be blocked.", code="assignment_not_blockable")
    draft_cycle = (
        DraftCycle.objects.filter(assignment=assignment, closed_at__isnull=True)
        .order_by("-cycle_no")
        .first()
    )
    report = BlockReport.objects.create(
        assignment=assignment,
        worker=actor,
        draft_cycle=draft_cycle,
        reason_schema_version=BLOCK_REASON_SCHEMA_VERSION,
        reason_code=reason_code,
        reason_text=clean_text,
    )
    assignment.work_state = Assignment.WorkState.BLOCKED
    assignment.queue_state = Assignment.QueueState.READY
    assignment.save(update_fields=["queue_state", "updated_at", "work_state"])
    return assignment, report


@transaction.atomic
def dispose_blocked_assignment(
    *,
    actor: User,
    assignment_id: UUID,
    action: str,
    reason: str,
    replacement_worker: User | None = None,
) -> tuple[Assignment, BlockDisposition]:
    clean_reason = reason.strip()
    if action not in BlockDisposition.Action.values:
        raise ValidationError("Unsupported block disposition.", code="block_disposition_invalid")
    if not clean_reason:
        raise ValidationError(
            "A block disposition reason is required.", code="block_disposition_reason_required"
        )
    if actor.role != User.Role.ADMIN or not actor.is_active:
        raise ResourceNotFound
    identity = (
        Assignment.objects.filter(assignment_id=assignment_id).values("batch_id", "task_id").first()
    )
    if identity is None:
        raise ResourceNotFound
    batch = WorkBatch.objects.select_for_update().get(batch_id=identity["batch_id"])
    task = Task.objects.select_for_update().get(task_id=identity["task_id"])
    assignment = (
        Assignment.objects.select_for_update()
        .select_related("worker")
        .get(assignment_id=assignment_id)
    )
    assignment.batch = batch
    assignment.task = task
    report = (
        BlockReport.objects.select_for_update()
        .filter(assignment=assignment, disposition__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if report is None or assignment.work_state != Assignment.WorkState.BLOCKED:
        raise ValidationError(
            "The assignment has no unresolved block.", code="block_disposition_conflict"
        )

    replacement = None
    if action in {BlockDisposition.Action.REOPEN, BlockDisposition.Action.REASSIGN}:
        _require_available_assignment(assignment)
    if action == BlockDisposition.Action.REOPEN:
        assignment.work_state = Assignment.WorkState.IN_PROGRESS
        assignment.queue_state = Assignment.QueueState.READY
        assignment.save(update_fields=["queue_state", "updated_at", "work_state"])
    elif action == BlockDisposition.Action.REASSIGN:
        if (
            replacement_worker is None
            or replacement_worker.role != User.Role.WORKER
            or not replacement_worker.is_active
        ):
            raise ValidationError(
                "Reassignment requires an active worker.", code="block_replacement_worker_invalid"
            )
        maximum_order = Assignment.objects.filter(batch=batch, worker=replacement_worker).aggregate(
            maximum=Max("order_index")
        )["maximum"]
        replacement = Assignment(
            batch=batch,
            task=task,
            worker=replacement_worker,
            order_index=0 if maximum_order is None else maximum_order + 1,
        )
        replacement.save()

    disposition = BlockDisposition.objects.create(
        block_report=report,
        actor=actor,
        action=action,
        reason=clean_reason,
        replacement_assignment=replacement,
    )
    record_audit_event(
        actor=actor,
        target_worker=assignment.worker,
        target_type="block_disposition",
        target_id=disposition.disposition_id,
        action="assignment.block_disposed",
        reason=clean_reason,
        details={
            "assignment_id": str(assignment.assignment_id),
            "block_id": str(report.block_id),
            "disposition": action,
            "replacement_assignment_id": (
                None if replacement is None else str(replacement.assignment_id)
            ),
        },
    )
    return assignment, disposition


def _require_editable_assignment(assignment: Assignment) -> None:
    if assignment.work_state != Assignment.WorkState.IN_PROGRESS:
        raise ValidationError("The assignment is not editable.", code="assignment_not_editable")


def _task_schema_version(task: Task) -> str:
    if task.meta_schema_version is None:
        raise ValidationError(
            "The task metadata contract is incomplete.", code="task_contract_invalid"
        )
    return task.meta_schema_version


def _require_usable_prediction(task: Task) -> PredictionArtifact | None:
    if task.mode != Task.Mode.SEMI:
        return None
    artifact_id = task.prediction_artifact_id
    asset_id = task.asset_id
    if artifact_id is None or asset_id is None:
        raise ValidationError(
            "The frozen prediction is unavailable.",
            code="semi_prediction_unavailable",
        )
    artifact = PredictionArtifact.objects.filter(artifact_id=artifact_id).first()
    if artifact is None or not prediction_artifact_matches_contract(
        artifact,
        asset_id=asset_id,
        expected_sha256=task.prediction_artifact_sha256,
    ):
        raise ValidationError(
            "The frozen prediction is unavailable.",
            code="semi_prediction_unavailable",
        )
    return artifact


def _lock_or_create_current_draft(assignment: Assignment) -> CurrentDraft:
    prediction = _require_usable_prediction(assignment.task)
    cycle = (
        DraftCycle.objects.select_for_update().filter(assignment=assignment, closed_at=None).first()
    )
    if cycle is None:
        cycle = DraftCycle.objects.create(
            assignment=assignment,
            cycle_no=DraftCycle.objects.filter(assignment=assignment).count() + 1,
        )
    _attach_acknowledged_guidance(assignment=assignment, cycle=cycle)
    draft = CurrentDraft.objects.select_for_update().filter(draft_cycle=cycle).first()
    if draft is None:
        schema_version = _task_schema_version(assignment.task)
        if assignment.task.mode == Task.Mode.SEMI:
            if prediction is None:
                raise ValidationError(
                    "The frozen prediction is unavailable.", code="semi_prediction_unavailable"
                )
            state = deepcopy(prediction.state)
        else:
            state = empty_annotation_state(
                meta_schema_version=schema_version,
                task_mode=assignment.task.mode or "",
            )
        draft = CurrentDraft.objects.create(
            draft_cycle=cycle,
            state=state,
            state_sha=annotation_state_sha(
                state,
                meta_schema_version=schema_version,
                task_mode=assignment.task.mode or "",
            ),
        )
    return draft


@transaction.atomic
def get_or_create_owned_current_draft(
    *,
    actor: User,
    assignment_id: UUID,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> CurrentDraft:
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment = lock_owned_writable_assignment(actor=actor, assignment_id=assignment_id)
    _require_editable_assignment(assignment)
    return _lock_or_create_current_draft(assignment)


@transaction.atomic
def save_owned_current_draft(
    *,
    actor: User,
    assignment_id: UUID,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
    expected_draft_version: int,
    state: object,
) -> CurrentDraft:
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment = lock_owned_writable_assignment(actor=actor, assignment_id=assignment_id)
    _require_editable_assignment(assignment)
    schema_version = _task_schema_version(assignment.task)
    canonical = canonicalize_annotation_state(
        state,
        meta_schema_version=schema_version,
        task_mode=assignment.task.mode or "",
    )
    draft = _lock_or_create_current_draft(assignment)
    if draft.draft_version != expected_draft_version:
        raise ValidationError("The draft version is stale.", code="draft_conflict")
    draft.state = canonical
    draft.state_sha = annotation_state_sha(
        canonical,
        meta_schema_version=schema_version,
        task_mode=assignment.task.mode or "",
    )
    draft.draft_version += 1
    draft.save(update_fields=["draft_version", "state", "state_sha", "updated_at"])
    return draft


@transaction.atomic
def start_owned_revision_cycle(
    *,
    actor: User,
    assignment_id: UUID,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> Assignment:
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment = _lock_owned_assignment(actor=actor, assignment_id=assignment_id)
    _require_available_assignment(assignment)
    if assignment.work_state != Assignment.WorkState.SUBMITTED:
        raise ValidationError(
            "Only a submitted assignment can be revised.", code="assignment_not_submitted"
        )
    if assignment.review_state == Assignment.ReviewState.CHANGES_REQUESTED:
        raise ValidationError(
            "Reviewed feedback requires a rework request.",
            code="revision_feedback_requires_rework",
        )
    latest = (
        AnnotationRevision.objects.filter(assignment=assignment).order_by("-revision_no").first()
    )
    if latest is None:
        raise ValidationError("The assignment has no revision.", code="revision_not_found")
    _create_revision_cycle(assignment=assignment, source_revision=latest)
    return assignment


def _create_revision_cycle(
    *, assignment: Assignment, source_revision: AnnotationRevision
) -> DraftCycle:
    cycle = DraftCycle.objects.create(
        assignment=assignment,
        cycle_no=source_revision.draft_cycle.cycle_no + 1,
    )
    _attach_acknowledged_guidance(assignment=assignment, cycle=cycle)
    CurrentDraft.objects.create(
        draft_cycle=cycle,
        state=deepcopy(source_revision.state),
        state_sha=source_revision.state_sha,
    )
    assignment.work_state = Assignment.WorkState.IN_PROGRESS
    assignment.save(update_fields=["updated_at", "work_state"])
    return cycle


def _attach_acknowledged_guidance(*, assignment: Assignment, cycle: DraftCycle) -> None:
    GuidanceEvent.objects.filter(
        acknowledged_at__isnull=False,
        assignment=assignment,
        feedback_draft_cycle__isnull=True,
    ).update(feedback_draft_cycle=cycle)


def rework_request_status(rework: ReworkRequest, *, now: datetime | None = None) -> str:
    if rework.completed_revision_id is not None:
        return "completed"
    if rework.due_at <= (now or timezone.now()):
        return "rework_overdue"
    if rework.draft_cycle_id is not None:
        return "in_progress"
    return "pending"


def list_owned_rework_requests(*, actor: User) -> list[ReworkRequest]:
    if not actor.is_active or actor.role != User.Role.WORKER:
        raise ResourceNotFound
    return list(ReworkRequest.objects.select_related("assignment").filter(assignment__worker=actor))


def list_owned_guidance_events(*, actor: User) -> list[GuidanceEvent]:
    if not actor.is_active or actor.role != User.Role.WORKER:
        raise ResourceNotFound
    return list(
        GuidanceEvent.objects.select_related("actor", "assignment__batch", "assignment__task")
        .filter(assignment__worker=actor)
        .order_by("created_at", "guidance_id")
    )


@transaction.atomic
def acknowledge_owned_guidance_event(*, actor: User, guidance_id: UUID) -> GuidanceEvent:
    guidance = (
        GuidanceEvent.objects.select_for_update()
        .select_related("actor", "assignment__batch", "assignment__task")
        .filter(guidance_id=guidance_id, assignment__worker=actor)
        .first()
    )
    if guidance is None or not actor.is_active or actor.role != User.Role.WORKER:
        raise ResourceNotFound
    if guidance.acknowledged_at is not None:
        return guidance
    guidance.acknowledged_at = timezone.now()
    guidance.feedback_draft_cycle = (
        DraftCycle.objects.select_for_update()
        .filter(assignment=guidance.assignment, closed_at=None)
        .first()
    )
    guidance.save(update_fields=["acknowledged_at", "feedback_draft_cycle"])
    return guidance


@transaction.atomic
def accept_owned_rework_request(
    *,
    actor: User,
    request_id: UUID,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> ReworkRequest:
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment_id = (
        ReworkRequest.objects.filter(request_id=request_id, assignment__worker=actor)
        .values_list("assignment_id", flat=True)
        .first()
    )
    if assignment_id is None:
        raise ResourceNotFound
    assignment = _lock_owned_assignment(actor=actor, assignment_id=assignment_id)
    rework = (
        ReworkRequest.objects.select_for_update()
        .select_related("initial_submission_revision__draft_cycle")
        .filter(request_id=request_id, assignment=assignment)
        .first()
    )
    if rework is None:
        raise ResourceNotFound
    if rework.draft_cycle_id is not None:
        return rework
    _require_available_assignment(assignment)
    if assignment.work_state != Assignment.WorkState.SUBMITTED:
        raise ValidationError(
            "Only a submitted assignment can enter rework.", code="assignment_not_submitted"
        )
    latest_revision_id = (
        AnnotationRevision.objects.filter(assignment=assignment)
        .order_by("-revision_no")
        .values_list("revision_id", flat=True)
        .first()
    )
    if latest_revision_id != rework.initial_submission_revision_id:
        raise ValidationError("The rework request is stale.", code="rework_request_stale")
    rework.draft_cycle = _create_revision_cycle(
        assignment=assignment,
        source_revision=rework.initial_submission_revision,
    )
    rework.exposed_at = timezone.now()
    rework.save(update_fields=["draft_cycle", "exposed_at"])
    return rework


@transaction.atomic
def submit_owned_current_draft(
    *,
    actor: User,
    assignment_id: UUID,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
    expected_state_sha: str,
    idempotency_key: UUID,
    locale: str,
    client_build_sha: str,
    viewer_version: str,
    interaction_contract_version: str,
) -> tuple[AnnotationRevision, bool]:
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    if locale not in SUPPORTED_LOCALES:
        raise ValidationError(
            "The submission locale is not available in this POC.",
            code="submission_locale_unsupported",
        )
    assignment = _lock_owned_assignment(actor=actor, assignment_id=assignment_id)
    existing = (
        AnnotationRevision.objects.select_for_update()
        .filter(
            assignment=assignment,
            idempotency_key=idempotency_key,
        )
        .first()
    )
    if existing is not None:
        if (
            existing.state_sha != expected_state_sha
            or existing.submission_locale != locale
            or existing.client_build_sha != client_build_sha
            or existing.viewer_version != viewer_version
            or existing.interaction_contract_version != interaction_contract_version
        ):
            raise ValidationError(
                "The idempotency key was used for another state.",
                code="submission_idempotency_conflict",
            )
        return existing, False
    _require_available_assignment(assignment)
    _require_editable_assignment(assignment)
    draft = _lock_or_create_current_draft(assignment)
    if draft.state_sha != expected_state_sha:
        raise ValidationError("The submitted state is stale.", code="draft_conflict")
    schema_version = _task_schema_version(assignment.task)
    canonical = validate_annotation_submission(
        draft.state,
        meta_schema_version=schema_version,
        task_mode=assignment.task.mode or "",
    )
    rework = ReworkRequest.objects.select_for_update().filter(draft_cycle=draft.draft_cycle).first()
    if rework is not None and (
        canonical["worker_scope_observation"] != "annotatable" or not canonical["pairs"]
    ):
        raise ValidationError(
            "Scope rework requires annotatable geometry.",
            code="rework_submission_incomplete",
        )
    if assignment.task.meta_schema_version is None or assignment.task.meta_copy_version is None:
        raise ValidationError(
            "The task metadata contract is incomplete.", code="task_contract_invalid"
        )
    revision = AnnotationRevision.objects.create(
        assignment=assignment,
        task=assignment.task,
        worker=assignment.worker,
        draft_cycle=draft.draft_cycle,
        revision_no=AnnotationRevision.objects.filter(assignment=assignment).count() + 1,
        state=canonical,
        state_sha=draft.state_sha,
        source_draft_version=draft.draft_version,
        meta_schema_version=assignment.task.meta_schema_version,
        meta_copy_version=assignment.task.meta_copy_version,
        submission_locale=locale,
        platform_release_id=PLATFORM_RELEASE_ID_V1,
        client_build_sha=client_build_sha,
        viewer_version=viewer_version,
        interaction_contract_version=interaction_contract_version,
        idempotency_key=idempotency_key,
    )
    if rework is not None:
        rework.completed_revision = revision
        rework.save(update_fields=["completed_revision"])
    enqueue_submission_assessment(revision)
    now = timezone.now()
    draft.draft_cycle.closed_at = now
    draft.draft_cycle.save(update_fields=["closed_at"])
    assignment.work_state = Assignment.WorkState.SUBMITTED
    assignment.queue_state = Assignment.QueueState.READY
    assignment.review_state = Assignment.ReviewState.UNREVIEWED
    assignment.save(update_fields=["queue_state", "review_state", "updated_at", "work_state"])
    return revision, True


def _require_admin(actor: User) -> None:
    if not actor.is_active or actor.role != User.Role.ADMIN:
        raise ResourceNotFound


def _clean_required_reason(reason: str, *, code: str) -> str:
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError("A reason is required.", code=code)
    if len(clean_reason) > 2_000:
        raise ValidationError("The reason is too long.", code=f"{code}_too_long")
    return clean_reason


def _clean_guidance_value(value: str, *, field: str, max_length: int) -> str:
    clean_value = value.strip()
    if not clean_value or len(clean_value) > max_length:
        raise ValidationError(f"Guidance {field} is invalid.", code=f"guidance_{field}_invalid")
    return clean_value


@transaction.atomic
def create_guidance_event(
    *,
    actor: User,
    assignment_id: UUID,
    channel: str,
    category: str,
    summary: str,
    source_revision_id: UUID | None,
) -> GuidanceEvent:
    _require_admin(actor)
    assignment = (
        Assignment.objects.select_related("batch", "task", "worker")
        .filter(assignment_id=assignment_id)
        .first()
    )
    if assignment is None:
        raise ResourceNotFound
    source_revision = None
    if source_revision_id is not None:
        source_revision = AnnotationRevision.objects.filter(
            assignment=assignment,
            revision_id=source_revision_id,
        ).first()
        if source_revision is None:
            raise ResourceNotFound
    guidance = GuidanceEvent.objects.create(
        actor=actor,
        assignment=assignment,
        category=_clean_guidance_value(category, field="category", max_length=64),
        channel=_clean_guidance_value(channel, field="channel", max_length=64),
        source_revision=source_revision,
        summary=_clean_guidance_value(summary, field="summary", max_length=500),
    )
    record_audit_event(
        actor=actor,
        target_worker=assignment.worker,
        target_type="guidance_event",
        target_id=guidance.guidance_id,
        action="guidance.created",
        reason="guidance_registered",
        details={"category": guidance.category, "channel": guidance.channel},
    )
    return guidance


@transaction.atomic
def create_rework_request(
    *,
    actor: User,
    initial_revision_id: UUID,
    source_adjudication_id: UUID,
    instruction: str,
    due_at: datetime,
) -> ReworkRequest:
    _require_admin(actor)
    clean_instruction = _clean_required_reason(instruction, code="rework_instruction_required")
    if not timezone.is_aware(due_at) or due_at <= timezone.now():
        raise ValidationError(
            "The rework due time must be in the future.", code="rework_due_invalid"
        )
    identity = (
        AnnotationRevision.objects.filter(revision_id=initial_revision_id)
        .values("assignment_id", "task_id")
        .first()
    )
    if identity is None:
        raise ResourceNotFound
    Task.objects.select_for_update().get(task_id=identity["task_id"])
    assignment = Assignment.objects.select_for_update().get(assignment_id=identity["assignment_id"])
    revision = AnnotationRevision.objects.select_for_update().get(revision_id=initial_revision_id)
    adjudication = AdjudicatedRevision.objects.filter(
        adjudication_id=source_adjudication_id,
        task_id=revision.task_id,
    ).first()
    if adjudication is None:
        raise ResourceNotFound
    if (
        revision.state.get("worker_scope_observation")
        not in {"needs_scope_review", "representation_oos"}
        or adjudication.state.get("worker_scope_observation") != "annotatable"
        or str(revision.revision_id) not in adjudication.source_revision_ids
        or assignment.review_state != Assignment.ReviewState.CHANGES_REQUESTED
    ):
        raise ValidationError(
            "The revision is not eligible for scope rework.", code="rework_request_invalid"
        )
    if ReworkRequest.objects.filter(initial_submission_revision=revision).exists():
        raise ValidationError("A rework request already exists.", code="rework_request_exists")
    rework = ReworkRequest.objects.create(
        actor=actor,
        assignment=assignment,
        due_at=due_at,
        initial_submission_revision=revision,
        instruction=clean_instruction,
        source_adjudication=adjudication,
    )
    record_audit_event(
        actor=actor,
        target_worker=revision.worker,
        target_type="rework_request",
        target_id=rework.request_id,
        action="rework.created",
        reason=clean_instruction,
        details={
            "request_id": str(rework.request_id),
            "revision_id": str(revision.revision_id),
        },
    )
    return rework


@transaction.atomic
def review_revision(
    *,
    actor: User,
    revision_id: UUID,
    outcome: str,
    reason: str,
) -> ReviewRecord:
    _require_admin(actor)
    if outcome not in ReviewRecord.Outcome.values:
        raise ValidationError("Unsupported review outcome.", code="review_outcome_invalid")
    clean_reason = reason.strip()
    if outcome == ReviewRecord.Outcome.CHANGES_REQUESTED and not clean_reason:
        raise ValidationError("Changes requested requires a reason.", code="review_reason_required")
    if len(clean_reason) > 2_000:
        raise ValidationError("The review reason is too long.", code="review_reason_too_long")

    identity = (
        AnnotationRevision.objects.filter(revision_id=revision_id)
        .values("assignment_id", "task_id")
        .first()
    )
    if identity is None:
        raise ResourceNotFound
    Task.objects.select_for_update().get(task_id=identity["task_id"])
    assignment = Assignment.objects.select_for_update().get(assignment_id=identity["assignment_id"])
    revision = (
        AnnotationRevision.objects.select_for_update()
        .select_related("worker")
        .get(revision_id=revision_id)
    )
    previous = (
        ReviewRecord.objects.select_for_update()
        .filter(revision=revision, successor__isnull=True)
        .first()
    )
    review = ReviewRecord.objects.create(
        revision=revision,
        reviewer=actor,
        outcome=outcome,
        reason=clean_reason,
        rule_version=REVIEW_RULE_VERSION,
        supersedes=previous,
    )
    latest_revision_id = (
        AnnotationRevision.objects.filter(assignment=assignment)
        .order_by("-revision_no")
        .values_list("revision_id", flat=True)
        .first()
    )
    if latest_revision_id == revision.revision_id:
        assignment.review_state = outcome
        assignment.queue_state = (
            Assignment.QueueState.NEEDS_REVISIT
            if outcome == ReviewRecord.Outcome.CHANGES_REQUESTED
            else Assignment.QueueState.READY
        )
        assignment.save(update_fields=["queue_state", "review_state", "updated_at"])
    record_audit_event(
        actor=actor,
        target_worker=revision.worker,
        target_type="review_record",
        target_id=review.review_id,
        action="revision.reviewed",
        reason=clean_reason or outcome,
        details={
            "outcome": outcome,
            "review_id": str(review.review_id),
            "revision_id": str(revision.revision_id),
        },
    )
    return review


def _latest_delivery_selection(task: Task) -> TaskDeliverySelection | None:
    return (
        TaskDeliverySelection.objects.select_for_update()
        .filter(task=task, successor__isnull=True)
        .first()
    )


def _create_adjudicated_revision(
    *,
    actor: User,
    task_id: UUID,
    state: object,
    source_revision_ids: list[UUID],
    reason: str,
) -> tuple[AdjudicatedRevision, Task]:
    _require_admin(actor)
    clean_reason = _clean_required_reason(reason, code="adjudication_reason_required")
    if not source_revision_ids or len(set(source_revision_ids)) != len(source_revision_ids):
        raise ValidationError(
            "Adjudication sources must be unique and non-empty.",
            code="adjudication_sources_invalid",
        )
    task = Task.objects.select_for_update().filter(task_id=task_id).first()
    if task is None:
        raise ResourceNotFound
    sources = list(
        AnnotationRevision.objects.select_related("worker").filter(
            task=task, revision_id__in=source_revision_ids
        )
    )
    sources_by_id = {source.revision_id: source for source in sources}
    if len(sources_by_id) != len(source_revision_ids):
        raise ValidationError(
            "All adjudication sources must belong to the task.",
            code="adjudication_sources_invalid",
        )
    schema_version = _task_schema_version(task)
    canonical = validate_annotation_submission(
        state,
        meta_schema_version=schema_version,
        task_mode=task.mode or "",
    )
    if task.meta_copy_version is None:
        raise ValidationError(
            "The task metadata contract is incomplete.", code="task_contract_invalid"
        )
    adjudication = AdjudicatedRevision.objects.create(
        task=task,
        actor=actor,
        state=canonical,
        state_sha=annotation_state_sha(
            canonical,
            meta_schema_version=schema_version,
            task_mode=task.mode or "",
        ),
        source_revision_ids=[str(revision_id) for revision_id in source_revision_ids],
        meta_schema_version=schema_version,
        meta_copy_version=task.meta_copy_version,
        reason=clean_reason,
        rule_version=ADJUDICATION_RULE_VERSION,
    )
    correlation_id = uuid4()
    for source in sources_by_id.values():
        record_audit_event(
            actor=actor,
            target_worker=source.worker,
            target_type="adjudicated_revision",
            target_id=adjudication.adjudication_id,
            action="task.adjudicated",
            reason=clean_reason,
            correlation_id=correlation_id,
            details={
                "adjudication_id": str(adjudication.adjudication_id),
                "source_revision_ids": adjudication.source_revision_ids,
                "task_id": str(task.task_id),
            },
        )
    return adjudication, task


@transaction.atomic
def adjudicate_task(
    *,
    actor: User,
    task_id: UUID,
    state: object,
    source_revision_ids: list[UUID],
    reason: str,
) -> tuple[AdjudicatedRevision, TaskDeliverySelection]:
    adjudication, task = _create_adjudicated_revision(
        actor=actor,
        task_id=task_id,
        state=state,
        source_revision_ids=source_revision_ids,
        reason=reason,
    )
    selection = TaskDeliverySelection.objects.create(
        task=task,
        actor=actor,
        adjudicated_revision=adjudication,
        reason=adjudication.reason,
        supersedes=_latest_delivery_selection(task),
    )
    return adjudication, selection


@transaction.atomic
def create_scope_rework_flow(
    *,
    actor: User,
    initial_revision_id: UUID,
    reason: str,
    instruction: str,
    due_at: datetime,
) -> tuple[ReviewRecord, AdjudicatedRevision, ReworkRequest]:
    """Create the narrow scope-only review flow without accepting client geometry."""
    _require_admin(actor)
    revision = (
        AnnotationRevision.objects.select_related("task")
        .filter(revision_id=initial_revision_id)
        .first()
    )
    if revision is None:
        raise ResourceNotFound
    if revision.state.get("worker_scope_observation") not in {
        "needs_scope_review",
        "representation_oos",
    }:
        raise ValidationError(
            "The revision is not eligible for scope rework.",
            code="scope_rework_revision_ineligible",
        )

    adjudicated_state = deepcopy(revision.state)
    adjudicated_state.update(
        {
            "scope_reason_codes": [],
            "scope_reason_text": "",
            "worker_scope_observation": "annotatable",
        }
    )
    review = review_revision(
        actor=actor,
        revision_id=revision.revision_id,
        outcome=ReviewRecord.Outcome.CHANGES_REQUESTED,
        reason=reason,
    )
    adjudication, _task = _create_adjudicated_revision(
        actor=actor,
        task_id=revision.task_id,
        state=adjudicated_state,
        source_revision_ids=[revision.revision_id],
        reason=reason,
    )
    rework = create_rework_request(
        actor=actor,
        initial_revision_id=revision.revision_id,
        source_adjudication_id=adjudication.adjudication_id,
        instruction=instruction,
        due_at=due_at,
    )
    return review, adjudication, rework


@transaction.atomic
def select_worker_revision_for_delivery(
    *,
    actor: User,
    task_id: UUID,
    worker_revision_id: UUID,
    reason: str,
) -> TaskDeliverySelection:
    _require_admin(actor)
    clean_reason = _clean_required_reason(reason, code="delivery_selection_reason_required")
    task = Task.objects.select_for_update().filter(task_id=task_id).first()
    if task is None:
        raise ResourceNotFound
    revision = (
        AnnotationRevision.objects.select_related("worker")
        .filter(task=task, revision_id=worker_revision_id)
        .first()
    )
    if revision is None:
        raise ResourceNotFound
    latest_review = ReviewRecord.objects.filter(revision=revision, successor__isnull=True).first()
    if latest_review is None or latest_review.outcome != ReviewRecord.Outcome.ACCEPTED:
        raise ValidationError(
            "Only an accepted worker revision can be selected.",
            code="delivery_revision_not_accepted",
        )
    selection = TaskDeliverySelection.objects.create(
        task=task,
        actor=actor,
        worker_revision=revision,
        reason=clean_reason,
        supersedes=_latest_delivery_selection(task),
    )
    record_audit_event(
        actor=actor,
        target_worker=revision.worker,
        target_type="task_delivery_selection",
        target_id=selection.selection_id,
        action="task.delivery_selected",
        reason=clean_reason,
        details={
            "revision_id": str(revision.revision_id),
            "selection_id": str(selection.selection_id),
            "task_id": str(task.task_id),
        },
    )
    return selection


def get_revision_for_admin(*, actor: User, revision_id: UUID) -> AnnotationRevision:
    if not actor.is_active or actor.role != User.Role.ADMIN:
        raise ResourceNotFound
    revision = (
        AnnotationRevision.objects.select_related("assignment", "task", "worker")
        .filter(revision_id=revision_id)
        .first()
    )
    if revision is None:
        raise ResourceNotFound
    record_audit_event(
        actor=actor,
        target_worker=revision.worker,
        target_type="annotation_revision",
        target_id=revision.revision_id,
        action="resource.sensitive_read",
        reason="administrator_read",
        details={"resource_id": str(revision.revision_id), "resource_kind": "revision"},
    )
    return revision


@transaction.atomic
def create_task_draft(
    *,
    asset: Asset,
    media_variants: Iterable[MediaVariant],
    mode: str,
    external_task_key: str = "",
    dataset_source: str = "",
    import_batch_key: str = "",
    previous_round_task: Task | None = None,
    prediction_artifact: PredictionArtifact | None = None,
    source_media_import_preview: MediaImportPreview | None = None,
) -> Task:
    variants = tuple(media_variants)
    if not variants:
        raise ValidationError(
            "A task requires at least one media variant.", code="task_media_required"
        )
    if any(variant.asset_id != asset.asset_id for variant in variants):
        raise ValidationError(
            "All task media must belong to its asset.", code="task_media_mismatch"
        )
    if any(variant.published_at is None for variant in variants):
        raise ValidationError(
            "Task media must already be published.", code="task_media_unpublished"
        )
    display_policy = Task.display_policy_for_mode(mode)
    if display_policy is None:
        raise ValidationError("Unsupported task mode.", code="task_mode_invalid")
    if mode == Task.Mode.MANUAL and prediction_artifact is not None:
        raise ValidationError(
            "Manual tasks cannot bind predictions.", code="manual_prediction_forbidden"
        )
    if prediction_artifact is not None and prediction_artifact.asset_id != asset.asset_id:
        raise ValidationError(
            "The prediction belongs to another asset.", code="prediction_asset_mismatch"
        )

    task = Task.objects.create(
        asset=asset,
        mode=mode,
        meta_schema_version=META_SCHEMA_V1,
        meta_copy_version=META_COPY_V1,
        **display_policy,
        prediction_artifact_id=(
            None if prediction_artifact is None else prediction_artifact.artifact_id
        ),
        prediction_artifact_sha256=(
            "" if prediction_artifact is None else prediction_artifact.artifact_sha256
        ),
        external_task_key=external_task_key,
        dataset_source=dataset_source,
        import_batch_key=import_batch_key,
        previous_round_task=previous_round_task,
        source_media_import_preview=source_media_import_preview,
    )
    TaskMediaVariant.objects.bulk_create(
        TaskMediaVariant(task=task, media_variant=variant) for variant in variants
    )
    return task


@transaction.atomic
def publish_task(*, task_id: UUID) -> Task:
    task = Task.objects.select_for_update().get(task_id=task_id)
    if task.status != Task.Status.DRAFT:
        raise ValidationError("Only draft tasks can be published.", code="task_not_draft")
    if task.mode == Task.Mode.SEMI:
        artifact_id = task.prediction_artifact_id
        asset_id = task.asset_id
        if artifact_id is None or asset_id is None:
            raise ValidationError(
                "Semi tasks require a matching frozen PredictionArtifact.",
                code="semi_prediction_not_ready",
            )
        artifact = PredictionArtifact.objects.filter(
            artifact_id=artifact_id,
        ).first()
        if artifact is None or not prediction_artifact_matches_contract(
            artifact,
            asset_id=asset_id,
            expected_sha256=task.prediction_artifact_sha256,
        ):
            raise ValidationError(
                "Semi tasks require a matching frozen PredictionArtifact.",
                code="semi_prediction_not_ready",
            )
    if not TaskMediaVariant.objects.filter(task=task).exists():
        raise ValidationError("A task requires allowed media.", code="task_media_required")
    task.status = Task.Status.PUBLISHED
    task.published_at = timezone.now()
    task.save(update_fields=["published_at", "status"])
    return task


@transaction.atomic
def create_semi_task_from_prediction(
    *,
    actor: User,
    prediction_artifact_id: UUID,
    media_variant_ids: Iterable[UUID],
    previous_round_task_id: UUID | None = None,
) -> tuple[Task, bool]:
    _require_admin(actor)
    artifact = (
        PredictionArtifact.objects.select_for_update()
        .select_related("asset")
        .filter(artifact_id=prediction_artifact_id)
        .first()
    )
    if artifact is None:
        raise ResourceNotFound

    requested_ids = tuple(media_variant_ids)
    variants = list(
        MediaVariant.objects.select_for_update()
        .filter(media_variant_id__in=requested_ids)
        .order_by("role", "media_variant_id")
    )
    if len(variants) != len(requested_ids):
        raise ResourceNotFound

    requested_id_set = {item.media_variant_id for item in variants}
    previous_round_task: Task | None = None
    if previous_round_task_id is not None:
        previous_round_task = (
            Task.objects.select_for_update()
            .prefetch_related("allowed_media_variants")
            .filter(task_id=previous_round_task_id)
            .first()
        )
        if previous_round_task is None:
            raise ResourceNotFound
        if (
            previous_round_task.asset_id != artifact.asset_id
            or previous_round_task.status != Task.Status.PUBLISHED
            or previous_round_task.mode != Task.Mode.SEMI
            or previous_round_task.prediction_artifact_id != artifact.artifact_id
            or previous_round_task.prediction_artifact_sha256 != artifact.artifact_sha256
            or {item.media_variant_id for item in previous_round_task.allowed_media_variants.all()}
            != requested_id_set
        ):
            raise ValidationError(
                "The previous Semi Task does not match this annotation round.",
                code="semi_task_previous_round_invalid",
            )
    existing_tasks = (
        Task.objects.filter(
            asset=artifact.asset,
            mode=Task.Mode.SEMI,
            prediction_artifact_id=artifact.artifact_id,
            prediction_artifact_sha256=artifact.artifact_sha256,
            status=Task.Status.PUBLISHED,
            previous_round_task=previous_round_task,
        )
        .prefetch_related("allowed_media_variants")
        .order_by("created_at", "task_id")
    )
    for existing in existing_tasks:
        if {
            item.media_variant_id for item in existing.allowed_media_variants.all()
        } == requested_id_set:
            return existing, False

    draft = create_task_draft(
        asset=artifact.asset,
        media_variants=variants,
        mode=Task.Mode.SEMI,
        previous_round_task=previous_round_task,
        prediction_artifact=artifact,
    )
    task = publish_task(task_id=draft.task_id)
    record_audit_event(
        actor=actor,
        target_type="task",
        target_id=task.task_id,
        action="task.published",
        reason="prediction_artifact_binding",
        details={
            "asset_id": str(artifact.asset_id),
            "media_variant_ids": [str(item.media_variant_id) for item in variants],
            "prediction_artifact_id": str(artifact.artifact_id),
            "previous_round_task_id": (
                None if previous_round_task is None else str(previous_round_task.task_id)
            ),
        },
    )
    return task, True


@transaction.atomic
def tombstone_task(*, task_id: UUID, reason: str) -> Task:
    task = Task.objects.select_for_update().get(task_id=task_id)
    if task.status != Task.Status.DRAFT:
        raise ValidationError("Only draft tasks can be tombstoned.", code="task_not_draft")
    if not reason.strip():
        raise ValidationError("A tombstone reason is required.", code="task_reason_required")
    TaskMediaVariant.objects.filter(task=task).delete()
    task.asset = None
    task.mode = None
    task.meta_schema_version = None
    task.meta_copy_version = None
    task.prediction_exposed = False
    task.model_issue_enabled = False
    task.assist_enabled = False
    task.prediction_artifact_id = None
    task.prediction_artifact_sha256 = ""
    task.external_task_key = ""
    task.dataset_source = ""
    task.import_batch_key = ""
    task.previous_round_task = None
    task.source_media_import_preview = None
    task.status = Task.Status.TOMBSTONED
    task.terminal_reason = reason.strip()
    task.tombstoned_at = timezone.now()
    task.save()
    return task


@transaction.atomic
def cancel_task(*, task_id: UUID, reason: str) -> Task:
    task = Task.objects.select_for_update().get(task_id=task_id)
    clean_reason = reason.strip()
    if task.status == Task.Status.CANCELLED and task.terminal_reason == clean_reason:
        return task
    if task.status != Task.Status.PUBLISHED:
        raise ValidationError("Only published tasks can be cancelled.", code="task_not_published")
    if not clean_reason:
        raise ValidationError("A cancellation reason is required.", code="task_reason_required")
    task.status = Task.Status.CANCELLED
    task.terminal_reason = clean_reason
    task.cancelled_at = timezone.now()
    task.save(update_fields=["cancelled_at", "status", "terminal_reason"])
    _revoke_unfinished_assignments(task=task)
    return task


@transaction.atomic
def supersede_task(*, task_id: UUID, replacement_task_id: UUID, reason: str) -> Task:
    if task_id == replacement_task_id:
        raise ValidationError("A task cannot replace itself.", code="task_replacement_invalid")
    tasks = {
        task.task_id: task
        for task in Task.objects.select_for_update()
        .filter(task_id__in=(task_id, replacement_task_id))
        .order_by("task_id")
    }
    task = tasks.get(task_id)
    replacement = tasks.get(replacement_task_id)
    if task is None or replacement is None:
        raise ResourceNotFound
    clean_reason = reason.strip()
    if (
        task.status == Task.Status.SUPERSEDED
        and task.replacement_task_id == replacement_task_id
        and task.terminal_reason == clean_reason
    ):
        return task
    if task.status != Task.Status.PUBLISHED or replacement.status != Task.Status.PUBLISHED:
        raise ValidationError(
            "Both superseded and replacement tasks must be published.",
            code="task_not_published",
        )
    if not clean_reason:
        raise ValidationError("A supersede reason is required.", code="task_reason_required")
    task.status = Task.Status.SUPERSEDED
    task.replacement_task = replacement
    task.terminal_reason = clean_reason
    task.save(update_fields=["replacement_task", "status", "terminal_reason"])
    _revoke_unfinished_assignments(task=task)
    return task


def _revoke_unfinished_assignments(*, task: Task) -> None:
    Assignment.objects.filter(
        task=task,
        work_state__in=(
            Assignment.WorkState.ASSIGNED,
            Assignment.WorkState.BLOCKED,
            Assignment.WorkState.IN_PROGRESS,
        ),
    ).update(work_state=Assignment.WorkState.REVOKED, updated_at=timezone.now())


@transaction.atomic
def create_manual_annotation_round(
    *,
    actor: User,
    source_preview_id: UUID,
    asset: Asset,
    media_variants: Iterable[MediaVariant],
) -> Task:
    _require_admin(actor)
    source_preview = MediaImportPreview.objects.select_for_update().get(
        preview_id=source_preview_id
    )
    asset = Asset.objects.select_for_update().get(asset_id=asset.asset_id)
    if source_preview.cancelled_at is not None:
        raise ValidationError("The media preview was cancelled.", code="media_preview_cancelled")
    if source_preview.published_at is None or source_preview.published_asset_id is None:
        raise ValidationError(
            "The media preview has not been published.", code="media_preview_unpublished"
        )
    if source_preview.published_asset_id != asset.asset_id:
        raise ValidationError(
            "The media preview belongs to another asset.",
            code="media_preview_asset_mismatch",
        )
    existing = Task.objects.filter(source_media_import_preview=source_preview).first()
    if existing is not None:
        return existing
    previous_round = (
        Task.objects.filter(asset=asset, published_at__isnull=False)
        .order_by("-created_at", "-task_id")
        .first()
    )
    draft = create_task_draft(
        asset=asset,
        media_variants=media_variants,
        mode=Task.Mode.MANUAL,
        previous_round_task=previous_round,
        source_media_import_preview=source_preview,
    )
    task = publish_task(task_id=draft.task_id)
    record_audit_event(
        actor=actor,
        target_type="task",
        target_id=task.task_id,
        action="task.published",
        reason="media_import_annotation_round",
        details={
            "asset_id": str(asset.asset_id),
            "source_preview_id": str(source_preview.preview_id),
        },
    )
    return task
