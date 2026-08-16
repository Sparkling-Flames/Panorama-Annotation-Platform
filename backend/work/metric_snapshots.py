from __future__ import annotations

from datetime import timedelta
from typing import cast
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from identity.authorization import ResourceNotFound

from .jobs import (
    SUBMISSION_ASSESSMENT_RULE_V1,
    submission_assessment_input_sha,
)
from .manifests import PLATFORM_RELEASE_ID_V1, json_sha256
from .models import AnnotationRevision, MetricSnapshot, PredictionArtifact, WorkBatch

METRIC_SNAPSHOT_RULE_V1 = "metric-snapshot-v1"
METRIC_SNAPSHOT_CODE_V1 = "metric-snapshot-code-v1"
SNAPSHOT_LEASE = timedelta(minutes=5)


def _input_manifest(*, batch: WorkBatch, cutoff) -> dict[str, object]:  # type: ignore[no-untyped-def]
    revisions = list(
        AnnotationRevision.objects.filter(
            assignment__batch=batch,
            submitted_at__lte=cutoff,
        )
        .select_related("assignment", "task")
        .prefetch_related("submission_assessments", "task__allowed_media_variants")
        .order_by("submitted_at", "revision_id")
    )
    prediction_ids = {
        revision.task.prediction_artifact_id
        for revision in revisions
        if revision.task.prediction_artifact_id is not None
    }
    predictions = {
        artifact.artifact_id: artifact
        for artifact in PredictionArtifact.objects.filter(artifact_id__in=prediction_ids)
    }
    inputs: list[dict[str, object]] = []
    for revision in revisions:
        assessment = next(
            (
                item
                for item in revision.submission_assessments.all()
                if item.rule_version == SUBMISSION_ASSESSMENT_RULE_V1
                and item.input_sha256 == submission_assessment_input_sha(revision)
            ),
            None,
        )
        prediction_id = revision.task.prediction_artifact_id
        prediction = None if prediction_id is None else predictions.get(prediction_id)
        inputs.append(
            {
                "assignment_id": str(revision.assignment_id),
                "assessment": (
                    None
                    if assessment is None
                    else {
                        "assessment_id": str(assessment.assessment_id),
                        "assessment_sha256": assessment.assessment_sha256,
                        "structure_valid": assessment.structure_valid,
                    }
                ),
                "copy_version": revision.meta_copy_version,
                "media": [
                    {
                        "content_sha256": variant.content_sha256,
                        "coordinate_mapping": variant.coordinate_mapping,
                        "media_variant_id": str(variant.media_variant_id),
                        "object_version": variant.object_version,
                        "role": variant.role,
                    }
                    for variant in sorted(
                        revision.task.allowed_media_variants.all(),
                        key=lambda item: (item.role, str(item.media_variant_id)),
                    )
                ],
                "model": (
                    None
                    if prediction is None
                    else {
                        "artifact_id": str(prediction.artifact_id),
                        "artifact_sha256": prediction.artifact_sha256,
                        "checkpoint_sha256": prediction.checkpoint_sha256,
                        "model_name": prediction.model_name,
                        "model_version": prediction.model_version,
                    }
                ),
                "revision_id": str(revision.revision_id),
                "schema_version": revision.meta_schema_version,
                "state_sha": revision.state_sha,
                "submission_locale": revision.submission_locale,
                "task_id": str(revision.task_id),
                "task_mode": revision.task.mode,
                "worker_id": str(revision.worker_id),
            }
        )
    return {
        "assessment_rule_version": SUBMISSION_ASSESSMENT_RULE_V1,
        "code_version": METRIC_SNAPSHOT_CODE_V1,
        "inputs": inputs,
        "platform_release_id": PLATFORM_RELEASE_ID_V1,
        "rule_version": METRIC_SNAPSHOT_RULE_V1,
    }


@transaction.atomic
def request_metric_snapshot(*, batch_id: UUID) -> tuple[MetricSnapshot, bool, bool]:
    batch = WorkBatch.objects.select_for_update().filter(batch_id=batch_id).first()
    if batch is None:
        raise ResourceNotFound
    cutoff = timezone.now()
    manifest = _input_manifest(batch=batch, cutoff=cutoff)
    snapshot, created = MetricSnapshot.objects.get_or_create(
        batch=batch,
        input_sha256=json_sha256(manifest),
        rule_version=METRIC_SNAPSHOT_RULE_V1,
        code_version=METRIC_SNAPSHOT_CODE_V1,
        defaults={
            "cutoff": cutoff,
            "input_manifest": manifest,
            "platform_release_id": PLATFORM_RELEASE_ID_V1,
        },
    )
    reused = not created and snapshot.status == MetricSnapshot.Status.SUCCEEDED
    if not created and snapshot.status == MetricSnapshot.Status.FAILED:
        snapshot.status = MetricSnapshot.Status.PENDING
        snapshot.last_error_code = ""
        snapshot.save(update_fields=["last_error_code", "status", "updated_at"])
    return snapshot, created, reused


@transaction.atomic
def _claim_metric_snapshot(*, worker_id: str) -> MetricSnapshot | None:
    now = timezone.now()
    snapshot = (
        MetricSnapshot.objects.select_for_update(skip_locked=True)
        .filter(
            Q(status__in=(MetricSnapshot.Status.PENDING, MetricSnapshot.Status.FAILED))
            | Q(
                status=MetricSnapshot.Status.RUNNING,
                locked_at__lt=now - SNAPSHOT_LEASE,
            )
        )
        .order_by("created_at", "snapshot_id")
        .first()
    )
    if snapshot is None:
        return None
    snapshot.status = MetricSnapshot.Status.RUNNING
    snapshot.attempt_count += 1
    snapshot.locked_at = now
    snapshot.locked_by = worker_id[:128]
    snapshot.save(update_fields=["attempt_count", "locked_at", "locked_by", "status", "updated_at"])
    return snapshot


@transaction.atomic
def _complete_metric_snapshot(*, snapshot_id: UUID, worker_id: str) -> MetricSnapshot:
    snapshot = MetricSnapshot.objects.select_for_update().get(
        snapshot_id=snapshot_id,
        status=MetricSnapshot.Status.RUNNING,
        locked_by=worker_id[:128],
    )
    inputs = cast(list[dict[str, object]], snapshot.input_manifest["inputs"])
    support = 0
    missing = 0
    not_evaluable = 0
    for item in inputs:
        assessment = item["assessment"]
        if assessment is None:
            missing += 1
        elif cast(dict[str, object], assessment)["structure_valid"] is True:
            support += 1
        else:
            not_evaluable += 1
    snapshot.support = support
    snapshot.missing_count = missing
    snapshot.not_evaluable_count = not_evaluable
    snapshot.result_manifest = {
        "missing": missing,
        "not_evaluable": not_evaluable,
        "support": support,
        "total_revisions": len(inputs),
    }
    snapshot.status = MetricSnapshot.Status.SUCCEEDED
    snapshot.locked_at = None
    snapshot.locked_by = ""
    snapshot.last_error_code = ""
    snapshot.save(
        update_fields=[
            "last_error_code",
            "locked_at",
            "locked_by",
            "missing_count",
            "not_evaluable_count",
            "result_manifest",
            "status",
            "support",
            "updated_at",
        ]
    )
    return snapshot


@transaction.atomic
def _fail_metric_snapshot(*, snapshot_id: UUID, worker_id: str, error: Exception) -> MetricSnapshot:
    snapshot = MetricSnapshot.objects.select_for_update().get(
        snapshot_id=snapshot_id,
        status=MetricSnapshot.Status.RUNNING,
        locked_by=worker_id[:128],
    )
    snapshot.status = MetricSnapshot.Status.FAILED
    snapshot.locked_at = None
    snapshot.locked_by = ""
    snapshot.last_error_code = type(error).__name__[:128]
    snapshot.save(
        update_fields=[
            "last_error_code",
            "locked_at",
            "locked_by",
            "status",
            "updated_at",
        ]
    )
    return snapshot


def process_next_metric_snapshot(*, worker_id: str) -> MetricSnapshot | None:
    snapshot = _claim_metric_snapshot(worker_id=worker_id)
    if snapshot is None:
        return None
    try:
        return _complete_metric_snapshot(snapshot_id=snapshot.snapshot_id, worker_id=worker_id)
    except MetricSnapshot.DoesNotExist:
        return None
    except Exception as error:
        try:
            return _fail_metric_snapshot(
                snapshot_id=snapshot.snapshot_id,
                worker_id=worker_id,
                error=error,
            )
        except MetricSnapshot.DoesNotExist:
            return None
