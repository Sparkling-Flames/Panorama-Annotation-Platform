from __future__ import annotations

from collections.abc import Iterable
from typing import cast
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from identity.authorization import (
    OwnedResource,
    ResourceKind,
    ResourceNotFound,
    resolve_owned_resource,
)
from identity.models import User
from identity.services import lock_worker_workspace_for_write
from media.models import Asset, MediaImportPreview, MediaVariant

from .models import Assignment, Task, TaskMediaVariant, WorkBatch

DEFAULT_META_SCHEMA_VERSION = "v1"
DEFAULT_META_COPY_VERSION = "v1"


def create_work_batch(*, name: str) -> WorkBatch:
    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("A batch name is required.", code="batch_name_required")
    return WorkBatch.objects.create(name=clean_name)


@transaction.atomic
def assign_task(*, batch: WorkBatch, task: Task, worker: User) -> Assignment:
    batch = WorkBatch.objects.select_for_update().get(batch_id=batch.batch_id)
    if batch.status != WorkBatch.Status.OPEN:
        raise ValidationError("The batch is not open.", code="assignment_batch_unavailable")
    task = Task.objects.select_for_update().get(task_id=task.task_id)
    if task.status != Task.Status.PUBLISHED:
        raise ValidationError("The task is not published.", code="assignment_task_unavailable")
    assignment = Assignment(batch=batch, task=task, worker=worker)
    assignment.save()
    return assignment


def get_owned_assignment(*, actor: User, assignment_id: UUID) -> Assignment:
    def lookup(resource_id: str) -> OwnedResource | None:
        assignment = (
            Assignment.objects.select_related("batch", "task", "worker")
            .filter(assignment_id=resource_id)
            .first()
        )
        return cast(OwnedResource, assignment) if assignment is not None else None

    return cast(
        Assignment,
        resolve_owned_resource(
            actor=actor,
            resource_kind=ResourceKind.ASSIGNMENT,
            resource_id=str(assignment_id),
            lookup=lookup,
        ),
    )


def _lock_owned_writable_assignment(*, actor: User, assignment_id: UUID) -> Assignment:
    assignment = (
        Assignment.objects.select_for_update(of=("self",))
        .select_related("batch", "task", "worker")
        .filter(assignment_id=assignment_id, worker=actor)
        .first()
    )
    if assignment is None:
        raise ResourceNotFound
    if assignment.batch.status != WorkBatch.Status.OPEN:
        raise ValidationError("The batch is not open.", code="batch_not_open")
    if assignment.task.status != Task.Status.PUBLISHED:
        raise ValidationError("The task is not published.", code="assignment_task_unavailable")
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
    assignment = _lock_owned_writable_assignment(actor=actor, assignment_id=assignment_id)
    if assignment.work_state == Assignment.WorkState.ASSIGNED:
        assignment.work_state = Assignment.WorkState.IN_PROGRESS
        assignment.save(update_fields=["updated_at", "work_state"])
    elif assignment.work_state != Assignment.WorkState.IN_PROGRESS:
        raise ValidationError("The assignment cannot be opened.", code="assignment_not_editable")
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
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment = _lock_owned_writable_assignment(actor=actor, assignment_id=assignment_id)
    if assignment.work_state == Assignment.WorkState.REVOKED:
        raise ValidationError("The assignment is revoked.", code="assignment_revoked")
    if assignment.queue_state != queue_state:
        assignment.queue_state = queue_state
        assignment.save(update_fields=["queue_state", "updated_at"])
    return assignment


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

    task = Task.objects.create(
        asset=asset,
        mode=mode,
        meta_schema_version=DEFAULT_META_SCHEMA_VERSION,
        meta_copy_version=DEFAULT_META_COPY_VERSION,
        **display_policy,
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
        raise ValidationError(
            "Semi tasks require the future frozen PredictionArtifact service.",
            code="semi_prediction_not_ready",
        )
    if not TaskMediaVariant.objects.filter(task=task).exists():
        raise ValidationError("A task requires allowed media.", code="task_media_required")
    task.status = Task.Status.PUBLISHED
    task.published_at = timezone.now()
    task.save(update_fields=["published_at", "status"])
    return task


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
    task = tasks[task_id]
    replacement = tasks[replacement_task_id]
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
            Assignment.WorkState.IN_PROGRESS,
        ),
    ).update(work_state=Assignment.WorkState.REVOKED, updated_at=timezone.now())


@transaction.atomic
def create_manual_annotation_round(
    *,
    source_preview_id: UUID,
    asset: Asset,
    media_variants: Iterable[MediaVariant],
) -> Task:
    source_preview = MediaImportPreview.objects.select_for_update().get(
        preview_id=source_preview_id
    )
    asset = Asset.objects.select_for_update().get(asset_id=asset.asset_id)
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
    return publish_task(task_id=draft.task_id)
