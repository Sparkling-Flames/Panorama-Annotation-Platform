from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from identity.authorization import ResourceNotFound

from .manifests import PLATFORM_RELEASE_ID_V1, json_sha256
from .models import (
    AdjudicatedRevision,
    AnnotationRevision,
    Assignment,
    BatchExportSnapshot,
    PredictionArtifact,
    ReviewRecord,
    Task,
    TaskConsensusArtifact,
    TaskDeliverySelection,
    WorkBatch,
)

BATCH_EXPORT_RULE_V1 = "batch-export-v1"
BATCH_EXPORT_CODE_V1 = "batch-export-code-v1"
EXPORT_LEASE = timedelta(minutes=5)
_SOURCE_KEYS = {
    "adjudication_ids",
    "assignment_ids",
    "batch",
    "consensus_artifact_ids",
    "cutoff",
    "delivery_selection_ids",
    "platform_release_id",
    "review_ids",
    "revision_ids",
    "rule_version",
    "task_ids",
}


class BatchExportManifestMismatch(Exception):
    code = "batch_export_manifest_mismatch"


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _review_payload(review: ReviewRecord) -> dict[str, object]:
    return {
        "created_at": _iso(review.created_at),
        "outcome": review.outcome,
        "reason": review.reason,
        "review_id": str(review.review_id),
        "reviewer_id": str(review.reviewer.worker_id),
        "rule_version": review.rule_version,
        "supersedes_review_id": (
            None if review.supersedes_id is None else str(review.supersedes_id)
        ),
    }


def _revision_payload(revision: AnnotationRevision, *, review_ids: set[UUID]) -> dict[str, object]:
    reviews = (
        ReviewRecord.objects.filter(revision=revision, review_id__in=review_ids)
        .select_related("reviewer")
        .order_by("created_at", "review_id")
    )
    payload: dict[str, object] = {
        "copy_version": revision.meta_copy_version,
        "idempotency_key": str(revision.idempotency_key),
        "revision_id": str(revision.revision_id),
        "revision_no": revision.revision_no,
        "reviews": [_review_payload(review) for review in reviews],
        "schema_version": revision.meta_schema_version,
        "source_draft_version": revision.source_draft_version,
        "state": revision.state,
        "state_sha": revision.state_sha,
        "submission_locale": revision.submission_locale,
        "submitted_at": _iso(revision.submitted_at),
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


def _assignment_payload(
    assignment: Assignment,
    *,
    review_ids: set[UUID],
    revision_ids: set[UUID],
) -> tuple[dict[str, object], str | None]:
    revisions = list(
        AnnotationRevision.objects.filter(
            assignment=assignment, revision_id__in=revision_ids
        ).order_by("revision_no", "revision_id")
    )
    latest_revision_id = None if not revisions else str(revisions[-1].revision_id)
    return (
        {
            "assignment_id": str(assignment.assignment_id),
            "order_index": assignment.order_index,
            "revisions": [
                _revision_payload(revision, review_ids=review_ids) for revision in revisions
            ],
            "worker_id": str(assignment.worker.worker_id),
        },
        latest_revision_id,
    )


def _consensus_payload(artifact: TaskConsensusArtifact) -> dict[str, object]:
    return {
        "artifact_id": str(artifact.artifact_id),
        "component_manifest": artifact.component_manifest,
        "created_at": _iso(artifact.created_at),
        "geometry_medoid_revision_id": str(artifact.geometry_medoid_revision_id),
        "input_manifest": artifact.input_manifest,
        "input_sha256": artifact.input_sha256,
        "policy_version": artifact.policy_version,
        "portal_medoid_revision_id": (
            None
            if artifact.portal_medoid_revision_id is None
            else str(artifact.portal_medoid_revision_id)
        ),
        "similarity_version": artifact.similarity_version,
    }


def _adjudication_payload(adjudication: AdjudicatedRevision) -> dict[str, object]:
    return {
        "actor_id": str(adjudication.actor.worker_id),
        "adjudication_id": str(adjudication.adjudication_id),
        "copy_version": adjudication.meta_copy_version,
        "created_at": _iso(adjudication.created_at),
        "reason": adjudication.reason,
        "rule_version": adjudication.rule_version,
        "schema_version": adjudication.meta_schema_version,
        "source_revision_ids": adjudication.source_revision_ids,
        "state": adjudication.state,
        "state_sha": adjudication.state_sha,
    }


def _selection_payload(selection: TaskDeliverySelection) -> dict[str, object]:
    return {
        "actor_id": str(selection.actor.worker_id),
        "adjudicated_revision_id": (
            None
            if selection.adjudicated_revision_id is None
            else str(selection.adjudicated_revision_id)
        ),
        "created_at": _iso(selection.created_at),
        "reason": selection.reason,
        "selection_id": str(selection.selection_id),
        "supersedes_selection_id": (
            None if selection.supersedes_id is None else str(selection.supersedes_id)
        ),
        "worker_revision_id": (
            None if selection.worker_revision_id is None else str(selection.worker_revision_id)
        ),
    }


def _task_payload(
    task: Task,
    *,
    adjudication_ids: set[UUID],
    assignment_ids: set[UUID],
    batch: WorkBatch,
    consensus_artifact_ids: set[UUID],
    delivery_selection_ids: set[UUID],
    review_ids: set[UUID],
    revision_ids: set[UUID],
) -> tuple[dict[str, object], list[dict[str, str]], dict[str, str] | None]:
    assignments = list(
        Assignment.objects.filter(
            assignment_id__in=assignment_ids,
            batch=batch,
            task=task,
        )
        .select_related("worker")
        .order_by("worker__worker_id", "order_index", "assignment_id")
    )
    assignment_payloads: list[dict[str, object]] = []
    latest: list[dict[str, str]] = []
    for assignment in assignments:
        payload, revision_id = _assignment_payload(
            assignment,
            review_ids=review_ids,
            revision_ids=revision_ids,
        )
        assignment_payloads.append(payload)
        if revision_id is not None:
            latest.append(
                {
                    "assignment_id": str(assignment.assignment_id),
                    "revision_id": revision_id,
                    "task_id": str(task.task_id),
                }
            )

    selections = list(
        TaskDeliverySelection.objects.filter(
            selection_id__in=delivery_selection_ids,
            task=task,
        )
        .select_related("actor")
        .order_by("created_at", "selection_id")
    )
    selected = None
    if selections:
        current = selections[-1]
        source_type = (
            "worker_revision" if current.worker_revision_id is not None else "adjudicated_revision"
        )
        source_id = current.worker_revision_id or current.adjudicated_revision_id
        selected = {
            "selection_id": str(current.selection_id),
            "source_id": str(source_id),
            "source_type": source_type,
            "task_id": str(task.task_id),
        }

    variants = task.allowed_media_variants.order_by("role", "media_variant_id")
    prediction_payload: dict[str, object] = {}
    if task.prediction_artifact_id is not None:
        prediction = PredictionArtifact.objects.filter(
            artifact_id=task.prediction_artifact_id
        ).first()
        if prediction is None or prediction.artifact_sha256 != task.prediction_artifact_sha256:
            raise BatchExportManifestMismatch
        prediction_payload = {
            "prediction_artifact": {
                "artifact_id": str(prediction.artifact_id),
                "artifact_sha256": prediction.artifact_sha256,
                "checkpoint_sha256": prediction.checkpoint_sha256,
                "inference_config_sha256": prediction.inference_config_sha256,
                "model_name": prediction.model_name,
                "model_version": prediction.model_version,
            }
        }
    return (
        {
            "active_time_rule_version": task.active_time_rule_version,
            "adjudications": [
                _adjudication_payload(item)
                for item in AdjudicatedRevision.objects.filter(
                    adjudication_id__in=adjudication_ids,
                    task=task,
                )
                .select_related("actor")
                .order_by("created_at", "adjudication_id")
            ],
            "asset_id": None if task.asset_id is None else str(task.asset_id),
            "assignments": assignment_payloads,
            "consensus_artifacts": [
                _consensus_payload(item)
                for item in TaskConsensusArtifact.objects.filter(
                    artifact_id__in=consensus_artifact_ids,
                    batch=batch,
                    task=task,
                ).order_by("created_at", "artifact_id")
            ],
            "delivery_selections": [_selection_payload(item) for item in selections],
            "external_task_key": task.external_task_key,
            "import_batch_key": task.import_batch_key,
            "media_variants": [
                {
                    "asset_id": str(variant.asset_id),
                    "content_crc64ecma": variant.content_crc64ecma,
                    "content_length": variant.content_length,
                    "content_sha256": variant.content_sha256,
                    "coordinate_mapping": variant.coordinate_mapping,
                    "format": variant.format,
                    "height": variant.height,
                    "media_variant_id": str(variant.media_variant_id),
                    "object_version": variant.object_version,
                    "role": variant.role,
                    "width": variant.width,
                }
                for variant in variants
            ],
            "meta_copy_version": task.meta_copy_version,
            "meta_schema_version": task.meta_schema_version,
            "mode": task.mode,
            **prediction_payload,
            "previous_round_task_id": (
                None if task.previous_round_task_id is None else str(task.previous_round_task_id)
            ),
            "task_id": str(task.task_id),
        },
        latest,
        selected,
    )


def _source_manifest(*, batch: WorkBatch, cutoff: datetime) -> dict[str, object]:
    assignments = Assignment.objects.filter(batch=batch)
    assignment_ids = list(
        assignments.order_by("assignment_id").values_list("assignment_id", flat=True)
    )
    task_ids = list(assignments.order_by("task_id").values_list("task_id", flat=True).distinct())
    revisions = AnnotationRevision.objects.filter(
        assignment_id__in=assignment_ids,
        submitted_at__lte=cutoff,
    )
    revision_ids = list(revisions.order_by("revision_id").values_list("revision_id", flat=True))
    return {
        "adjudication_ids": [
            str(value)
            for value in AdjudicatedRevision.objects.filter(
                created_at__lte=cutoff,
                task_id__in=task_ids,
            )
            .order_by("adjudication_id")
            .values_list("adjudication_id", flat=True)
        ],
        "assignment_ids": [str(value) for value in assignment_ids],
        "batch": {
            "batch_id": str(batch.batch_id),
            "consensus_policy": batch.consensus_policy,
            "name": batch.name,
            "policy_frozen_at": _iso(batch.policy_frozen_at),
            "scope_policy": batch.scope_policy,
        },
        "consensus_artifact_ids": [
            str(value)
            for value in TaskConsensusArtifact.objects.filter(
                batch=batch,
                created_at__lte=cutoff,
                task_id__in=task_ids,
            )
            .order_by("artifact_id")
            .values_list("artifact_id", flat=True)
        ],
        "cutoff": _iso(cutoff),
        "delivery_selection_ids": [
            str(value)
            for value in TaskDeliverySelection.objects.filter(
                created_at__lte=cutoff,
                task_id__in=task_ids,
            )
            .order_by("selection_id")
            .values_list("selection_id", flat=True)
        ],
        "platform_release_id": PLATFORM_RELEASE_ID_V1,
        "review_ids": [
            str(value)
            for value in ReviewRecord.objects.filter(
                created_at__lte=cutoff,
                revision_id__in=revision_ids,
            )
            .order_by("review_id")
            .values_list("review_id", flat=True)
        ],
        "revision_ids": [str(value) for value in revision_ids],
        "rule_version": BATCH_EXPORT_RULE_V1,
        "task_ids": [str(value) for value in task_ids],
    }


def _uuid_set(source: dict[str, object], field: str) -> set[UUID]:
    values = source.get(field)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise BatchExportManifestMismatch
    try:
        identifiers = {UUID(value) for value in values}
    except ValueError as error:
        raise BatchExportManifestMismatch from error
    if len(identifiers) != len(values):
        raise BatchExportManifestMismatch
    return identifiers


def _export_manifest(*, batch: WorkBatch, source: dict[str, object]) -> dict[str, object]:
    adjudication_ids = _uuid_set(source, "adjudication_ids")
    assignment_ids = _uuid_set(source, "assignment_ids")
    consensus_artifact_ids = _uuid_set(source, "consensus_artifact_ids")
    delivery_selection_ids = _uuid_set(source, "delivery_selection_ids")
    review_ids = _uuid_set(source, "review_ids")
    revision_ids = _uuid_set(source, "revision_ids")
    task_ids = _uuid_set(source, "task_ids")
    if (
        Task.objects.filter(task_id__in=task_ids).count() != len(task_ids)
        or Assignment.objects.filter(
            assignment_id__in=assignment_ids,
            batch=batch,
            task_id__in=task_ids,
        ).count()
        != len(assignment_ids)
        or AnnotationRevision.objects.filter(
            assignment__batch=batch,
            revision_id__in=revision_ids,
            task_id__in=task_ids,
        ).count()
        != len(revision_ids)
        or ReviewRecord.objects.filter(
            review_id__in=review_ids,
            revision_id__in=revision_ids,
        ).count()
        != len(review_ids)
        or AdjudicatedRevision.objects.filter(
            adjudication_id__in=adjudication_ids,
            task_id__in=task_ids,
        ).count()
        != len(adjudication_ids)
        or TaskConsensusArtifact.objects.filter(
            artifact_id__in=consensus_artifact_ids,
            batch=batch,
            task_id__in=task_ids,
        ).count()
        != len(consensus_artifact_ids)
        or TaskDeliverySelection.objects.filter(
            selection_id__in=delivery_selection_ids,
            task_id__in=task_ids,
        ).count()
        != len(delivery_selection_ids)
    ):
        raise BatchExportManifestMismatch

    task_payloads: list[dict[str, object]] = []
    latest: list[dict[str, str]] = []
    selected: list[dict[str, str]] = []
    for task in Task.objects.filter(task_id__in=task_ids).order_by("task_id"):
        payload, task_latest, task_selected = _task_payload(
            task,
            adjudication_ids=adjudication_ids,
            assignment_ids=assignment_ids,
            batch=batch,
            consensus_artifact_ids=consensus_artifact_ids,
            delivery_selection_ids=delivery_selection_ids,
            review_ids=review_ids,
            revision_ids=revision_ids,
        )
        task_payloads.append(payload)
        latest.extend(task_latest)
        if task_selected is not None:
            selected.append(task_selected)
    batch_payload = source.get("batch")
    if not isinstance(batch_payload, dict) or batch_payload.get("batch_id") != str(batch.batch_id):
        raise BatchExportManifestMismatch
    return {
        "artifact_type": "batch_export_snapshot",
        "authority": "batch_snapshot",
        "batch": batch_payload,
        "cutoff": source["cutoff"],
        "exported_at": source["cutoff"],
        "is_dataset_release": False,
        "is_final_gold": False,
        "platform_release_id": source["platform_release_id"],
        "rule_version": source["rule_version"],
        "tasks": task_payloads,
        "views": {"latest_submitted": latest, "selected_delivery": selected},
    }


def _source_sha(source: dict[str, object]) -> str:
    return json_sha256({key: value for key, value in source.items() if key != "cutoff"})


def _validate_source(source: object) -> dict[str, object]:
    if (
        not isinstance(source, dict)
        or set(source) != _SOURCE_KEYS
        or source.get("platform_release_id") != PLATFORM_RELEASE_ID_V1
        or source.get("rule_version") != BATCH_EXPORT_RULE_V1
        or not isinstance(source.get("batch"), dict)
        or not isinstance(source.get("cutoff"), str)
    ):
        raise BatchExportManifestMismatch
    return cast(dict[str, object], source)


@transaction.atomic
def request_batch_export(*, batch_id: UUID) -> tuple[BatchExportSnapshot, bool, bool]:
    batch = WorkBatch.objects.select_for_update().filter(batch_id=batch_id).first()
    if batch is None:
        raise ResourceNotFound
    cutoff = timezone.now()
    source = _source_manifest(batch=batch, cutoff=cutoff)
    snapshot, created = BatchExportSnapshot.objects.get_or_create(
        batch=batch,
        input_sha256=_source_sha(source),
        rule_version=BATCH_EXPORT_RULE_V1,
        code_version=BATCH_EXPORT_CODE_V1,
        defaults={
            "cutoff": cutoff,
            "input_manifest": source,
            "platform_release_id": PLATFORM_RELEASE_ID_V1,
        },
    )
    reused = not created and snapshot.status == BatchExportSnapshot.Status.SUCCEEDED
    if not created and snapshot.status == BatchExportSnapshot.Status.FAILED:
        snapshot.cutoff = cutoff
        snapshot.input_manifest = source
        snapshot.status = BatchExportSnapshot.Status.PENDING
        snapshot.last_error_code = ""
        snapshot.save(
            update_fields=[
                "cutoff",
                "input_manifest",
                "last_error_code",
                "status",
                "updated_at",
            ]
        )
    return snapshot, created, reused


@transaction.atomic
def _claim_batch_export(*, worker_id: str) -> BatchExportSnapshot | None:
    now = timezone.now()
    snapshot = (
        BatchExportSnapshot.objects.select_for_update(skip_locked=True)
        .filter(
            Q(status__in=(BatchExportSnapshot.Status.PENDING, BatchExportSnapshot.Status.FAILED))
            | Q(
                status=BatchExportSnapshot.Status.RUNNING,
                locked_at__lt=now - EXPORT_LEASE,
            )
        )
        .order_by("created_at", "export_id")
        .first()
    )
    if snapshot is None:
        return None
    snapshot.status = BatchExportSnapshot.Status.RUNNING
    snapshot.attempt_count += 1
    snapshot.locked_at = now
    snapshot.locked_by = worker_id[:128]
    snapshot.save(update_fields=["attempt_count", "locked_at", "locked_by", "status", "updated_at"])
    return snapshot


@transaction.atomic
def _complete_batch_export(*, export_id: UUID, worker_id: str) -> BatchExportSnapshot:
    snapshot = BatchExportSnapshot.objects.select_for_update().get(
        export_id=export_id,
        status=BatchExportSnapshot.Status.RUNNING,
        locked_by=worker_id[:128],
    )
    source = _validate_source(snapshot.input_manifest)
    if _source_sha(source) != snapshot.input_sha256:
        raise BatchExportManifestMismatch
    manifest = _export_manifest(batch=snapshot.batch, source=source)
    snapshot.export_manifest = manifest
    snapshot.export_sha256 = json_sha256(manifest)
    snapshot.status = BatchExportSnapshot.Status.SUCCEEDED
    snapshot.locked_at = None
    snapshot.locked_by = ""
    snapshot.last_error_code = ""
    snapshot.save(
        update_fields=[
            "export_manifest",
            "export_sha256",
            "last_error_code",
            "locked_at",
            "locked_by",
            "status",
            "updated_at",
        ]
    )
    return snapshot


@transaction.atomic
def _fail_batch_export(*, export_id: UUID, worker_id: str, error: Exception) -> BatchExportSnapshot:
    snapshot = BatchExportSnapshot.objects.select_for_update().get(
        export_id=export_id,
        status=BatchExportSnapshot.Status.RUNNING,
        locked_by=worker_id[:128],
    )
    snapshot.status = BatchExportSnapshot.Status.FAILED
    snapshot.locked_at = None
    snapshot.locked_by = ""
    snapshot.last_error_code = str(getattr(error, "code", type(error).__name__))[:128]
    snapshot.save(
        update_fields=["last_error_code", "locked_at", "locked_by", "status", "updated_at"]
    )
    return snapshot


def process_next_batch_export(*, worker_id: str) -> BatchExportSnapshot | None:
    snapshot = _claim_batch_export(worker_id=worker_id)
    if snapshot is None:
        return None
    try:
        return _complete_batch_export(export_id=snapshot.export_id, worker_id=worker_id)
    except Exception as error:
        return _fail_batch_export(
            export_id=snapshot.export_id,
            worker_id=worker_id,
            error=error,
        )
