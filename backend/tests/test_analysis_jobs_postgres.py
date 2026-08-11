import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import DatabaseError, close_old_connections, connection, transaction
from identity.models import User
from media.models import Asset, MediaVariant
from work.annotation_state import annotation_state_sha
from work.batch_exports import process_next_batch_export, request_batch_export
from work.jobs import (
    claim_analysis_job,
    eligible_task_input_manifest,
    enqueue_submission_assessment,
    process_next_analysis_job,
)
from work.metric_snapshots import process_next_metric_snapshot, request_metric_snapshot
from work.models import (
    AnnotationRevision,
    BatchExportSnapshot,
    CurrentDraft,
    DraftCycle,
    MetricSnapshot,
    Task,
    TaskConsensusArtifact,
    TaskEligibilityArtifact,
)
from work.services import (
    assign_task,
    create_task_draft,
    create_work_batch,
    freeze_work_batch,
    publish_task,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL SKIP LOCKED integration test",
    ),
]


def queued_revision() -> AnnotationRevision:
    worker = User.objects.create_user(username="job-claim-worker", role=User.Role.WORKER)
    asset = Asset.objects.create(source_key="analysis/job-claim")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key="cos://analysis/job-claim.jpg",
        object_version="v1",
        content_sha256="a" * 64,
        content_length=100,
        content_crc64ecma="11",
        width=20,
        height=10,
        format=MediaVariant.Format.JPEG,
        role=MediaVariant.Role.COMPRESSED,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    task = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=(variant,),
            mode=Task.Mode.MANUAL,
        ).task_id
    )
    assignment = assign_task(
        batch=create_work_batch(name="Job claim"),
        task=task,
        worker=worker,
    )
    cycle = DraftCycle.objects.create(assignment=assignment, cycle_no=1)
    state = {
        "difficulty": ["trivial"],
        "geometry_attempt_reason_text": "Image evidence is insufficient.",
        "geometry_attempt_status": "not_drawable",
        "pairs": [],
        "portals": [],
        "seam_anchor_pair_id": None,
        "scope_reason_codes": ["insufficient_evidence"],
        "scope_reason_text": "",
        "worker_scope_observation": "representation_oos",
    }
    state_sha = annotation_state_sha(
        state,
        meta_schema_version=task.meta_schema_version,
        task_mode=task.mode,
    )
    draft = CurrentDraft.objects.create(draft_cycle=cycle, state=state, state_sha=state_sha)
    revision = AnnotationRevision.objects.create(
        assignment=assignment,
        task=task,
        worker=worker,
        draft_cycle=cycle,
        revision_no=1,
        state=state,
        state_sha=draft.state_sha,
        source_draft_version=0,
        meta_schema_version=task.meta_schema_version,
        meta_copy_version=task.meta_copy_version,
        submission_locale="zh-CN",
        idempotency_key=uuid4(),
    )
    enqueue_submission_assessment(revision)
    return revision


def test_task_10_1_postgresql_workers_cannot_claim_the_same_job() -> None:
    revision = queued_revision()
    barrier = Barrier(2)

    def claim(worker_id: str) -> str | None:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            job = claim_analysis_job(worker_id=worker_id)
            return None if job is None else str(job.job_id)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, ("worker-one", "worker-two")))

    job_id = str(revision.analysis_jobs.get().job_id)
    assert claimed.count(job_id) == 1
    assert claimed.count(None) == 1


def test_task_10_4_postgresql_rejects_assessment_raw_mutation() -> None:
    revision = queued_revision()
    process_next_analysis_job(worker_id="assessment-trigger")
    assessment = revision.submission_assessments.get()

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE work_submissionassessment SET structure_valid = %s WHERE assessment_id = %s",
            [False, assessment.pk],
        )
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM work_submissionassessment WHERE assessment_id = %s",
            [assessment.pk],
        )


def test_task_10_6_postgresql_freezes_scope_policy_and_eligibility_artifact() -> None:
    revision = queued_revision()
    process_next_analysis_job(worker_id="scope-trigger")
    batch = revision.assignment.batch
    manifest = eligible_task_input_manifest(
        task_id=revision.task_id,
        batch_id=batch.batch_id,
    )
    input_sha256 = sha256(
        json.dumps(
            {"input_manifest": manifest, "scope_policy": batch.scope_policy},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    artifact = TaskEligibilityArtifact.objects.create(
        batch=batch,
        task=revision.task,
        policy_version=batch.scope_policy["version"],
        input_manifest=manifest,
        input_sha256=input_sha256,
        outcome="representation_oos",
        reason_code="insufficient_evidence",
        support=1,
    )
    freeze_work_batch(batch_id=batch.batch_id)

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE work_workbatch SET scope_policy = %s::jsonb WHERE batch_id = %s",
            ["{}", batch.pk],
        )
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE work_workbatch SET consensus_policy = %s::jsonb WHERE batch_id = %s",
            ["{}", batch.pk],
        )
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM work_taskeligibilityartifact WHERE artifact_id = %s",
            [artifact.pk],
        )


def test_task_10_8_postgresql_rejects_consensus_artifact_raw_mutation() -> None:
    revision = queued_revision()
    batch = revision.assignment.batch
    artifact = TaskConsensusArtifact.objects.create(
        batch=batch,
        task=revision.task,
        policy_version="consensus-policy-v2",
        similarity_version="canonical-2d-consensus-v1",
        input_manifest={"revision_ids": [str(revision.revision_id)]},
        input_sha256="a" * 64,
        component_manifest={"geometry": {"medoid_revision_id": str(revision.revision_id)}},
        geometry_medoid_revision=revision,
    )

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE work_taskconsensusartifact SET input_sha256 = %s WHERE artifact_id = %s",
            ["b" * 64, artifact.pk],
        )
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM work_taskconsensusartifact WHERE artifact_id = %s",
            [artifact.pk],
        )


def test_task_12_3_postgresql_rejects_completed_metric_snapshot_raw_mutation() -> None:
    batch = create_work_batch(name="PostgreSQL metric snapshot")
    snapshot, created, reused = request_metric_snapshot(batch_id=batch.batch_id)
    completed = process_next_metric_snapshot(worker_id="metric-trigger")

    assert created is True
    assert reused is False
    assert completed is not None
    assert completed.snapshot_id == snapshot.snapshot_id
    assert completed.status == MetricSnapshot.Status.SUCCEEDED
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE work_metricsnapshot SET support = %s WHERE snapshot_id = %s",
            [99, snapshot.pk],
        )
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM work_metricsnapshot WHERE snapshot_id = %s",
            [snapshot.pk],
        )


def test_task_12_10_postgresql_rejects_completed_batch_export_raw_mutation() -> None:
    revision = queued_revision()
    snapshot, created, reused = request_batch_export(batch_id=revision.assignment.batch_id)
    completed = process_next_batch_export(worker_id="batch-export-trigger")

    assert created is True
    assert reused is False
    assert completed is not None
    assert completed.export_id == snapshot.export_id
    assert completed.status == BatchExportSnapshot.Status.SUCCEEDED
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE work_batchexportsnapshot SET export_sha256 = %s WHERE export_id = %s",
            ["f" * 64, snapshot.pk],
        )
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM work_batchexportsnapshot WHERE export_id = %s",
            [snapshot.pk],
        )
