from __future__ import annotations

from datetime import timedelta
from io import StringIO
from uuid import uuid4

import pytest
from activity.models import ActivityEvent
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import Client
from django.utils import timezone
from identity.models import ActiveWorkspace, AuditEvent, DataNoticeAcceptance, User
from identity.services import CURRENT_DATA_NOTICE_VERSION
from media.models import Asset, MediaVariant
from work.batch_exports import process_next_batch_export, request_batch_export
from work.jobs import submission_assessment_input_sha
from work.manifests import json_sha256
from work.metric_snapshots import process_next_metric_snapshot, request_metric_snapshot
from work.models import (
    AnnotationRevision,
    Assignment,
    BatchExportSnapshot,
    BlockReport,
    MetricSnapshot,
    OperationalIssue,
    ReviewRecord,
    SubmissionAssessment,
    Task,
    TaskAggregate,
    TaskConsensusArtifact,
    TaskDeliverySelection,
    WorkBatch,
)
from work.operations import record_operational_issue
from work.services import (
    assign_task,
    create_task_draft,
    create_work_batch,
    open_owned_assignment,
    publish_task,
    review_revision,
    save_owned_current_draft,
    select_worker_revision_for_delivery,
    submit_owned_current_draft,
)

pytestmark = pytest.mark.django_db


def _published_task(asset: Asset, variant: MediaVariant, suffix: str) -> Task:
    return publish_task(
        task_id=create_task_draft(
            asset=asset,
            external_task_key=f"operations-{suffix}",
            media_variants=(variant,),
            mode=Task.Mode.MANUAL,
        ).task_id
    )


def _submitted_assignment(*, batch: WorkBatch, task: Task, suffix: str) -> AnnotationRevision:
    worker = User.objects.create_user(
        username=f"operations-{suffix}",
        role=User.Role.WORKER,
        must_change_password=False,
    )
    DataNoticeAcceptance.objects.create(worker=worker, notice_version=CURRENT_DATA_NOTICE_VERSION)
    assignment = assign_task(batch=batch, task=task, worker=worker)
    tab_id = uuid4()
    workspace = ActiveWorkspace.objects.create(
        worker=worker,
        session_key=f"operations-{suffix}-session",
        client_instance_id=uuid4(),
        tab_id=tab_id,
        lease_expires_at=timezone.now() + timedelta(minutes=5),
    )
    workspace_args = {
        "session_key": workspace.session_key,
        "session_token": str(workspace.token),
        "tab_id": tab_id,
    }
    open_owned_assignment(actor=worker, assignment_id=assignment.assignment_id, **workspace_args)
    draft = save_owned_current_draft(
        actor=worker,
        assignment_id=assignment.assignment_id,
        expected_draft_version=0,
        state={
            "difficulty": ["trivial"],
            "geometry_attempt_reason_text": "Image evidence is insufficient.",
            "geometry_attempt_status": "not_drawable",
            "pairs": [],
            "portals": [],
            "seam_anchor_pair_id": None,
            "scope_reason_codes": ["insufficient_evidence"],
            "scope_reason_text": "",
            "worker_scope_observation": "representation_oos",
        },
        **workspace_args,
    )
    revision, created = submit_owned_current_draft(
        actor=worker,
        assignment_id=assignment.assignment_id,
        expected_state_sha=draft.state_sha,
        idempotency_key=uuid4(),
        locale="zh-CN",
        client_build_sha="test-client-build",
        viewer_version="annotation-viewer-v1",
        interaction_contract_version="annotation-interaction-v1",
        **workspace_args,
    )
    assert created
    return revision


def _export_fixture() -> tuple[
    User,
    WorkBatch,
    TaskConsensusArtifact,
    ReviewRecord,
    AnnotationRevision,
    TaskDeliverySelection,
    MediaVariant,
]:
    asset = Asset.objects.create(source_key="exports/source")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key="cos://exports/compressed.jpg",
        object_version="exports-v1",
        content_sha256="e" * 64,
        content_length=321,
        content_crc64ecma="321",
        width=20,
        height=10,
        format=MediaVariant.Format.JPEG,
        role=MediaVariant.Role.COMPRESSED,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    batch = create_work_batch(name="Export batch")
    revision = _submitted_assignment(
        batch=batch,
        task=_published_task(asset, variant, "export"),
        suffix="export",
    )
    administrator = User.objects.create_user(username="export-admin", role=User.Role.ADMIN)
    review = review_revision(
        actor=administrator,
        revision_id=revision.revision_id,
        outcome=ReviewRecord.Outcome.ACCEPTED,
        reason="",
    )
    consensus = TaskConsensusArtifact.objects.create(
        batch=batch,
        task=revision.task,
        policy_version="consensus-policy-v2",
        similarity_version="canonical-2d-consensus-v1",
        input_manifest={"inputs": [{"revision_id": str(revision.revision_id)}]},
        input_sha256="c" * 64,
        component_manifest={"geometry": {"state": "resolved"}, "portal": {"state": "resolved"}},
        geometry_medoid_revision=revision,
        portal_medoid_revision=revision,
    )
    selection = select_worker_revision_for_delivery(
        actor=administrator,
        task_id=revision.task_id,
        worker_revision_id=revision.revision_id,
        reason="Selected for export.",
    )
    return administrator, batch, consensus, review, revision, selection, variant


def test_pap_aae_sc_001_pap_aae_sc_011_admin_reads_real_batch_operational_counts() -> None:
    asset = Asset.objects.create(source_key="operations/source")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key="cos://operations/compressed.jpg",
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
    batch = create_work_batch(name="Operations batch")
    revisions = [
        _submitted_assignment(
            batch=batch,
            task=_published_task(asset, variant, suffix),
            suffix=suffix,
        )
        for suffix in ("resolved", "unresolved", "pending")
    ]
    resolved, unresolved, pending = revisions
    administrator = User.objects.create_user(username="operations-admin", role=User.Role.ADMIN)
    review_revision(
        actor=administrator,
        revision_id=resolved.revision_id,
        outcome=ReviewRecord.Outcome.ACCEPTED,
        reason="",
    )
    SubmissionAssessment.objects.create(
        revision=resolved,
        rule_version="submission-assessment-v1",
        input_sha256=resolved.state_sha,
        structure_valid=False,
        component_manifest={},
        assessment_sha256="b" * 64,
    )
    for revision, state in (
        (resolved, TaskAggregate.ScopeState.RESOLVED_OOS),
        (unresolved, TaskAggregate.ScopeState.UNRESOLVED),
    ):
        TaskAggregate.objects.create(
            batch=batch,
            task=revision.task,
            policy_version="scope-policy-v1",
            input_manifest={"revision_ids": [str(revision.revision_id)]},
            input_sha256=submission_assessment_input_sha(revision),
            scope_state=state,
            scope_reason_code="insufficient_evidence",
            scope_support=1,
            terminal_state=(
                TaskAggregate.TerminalState.RESOLVED
                if state == TaskAggregate.ScopeState.RESOLVED_OOS
                else TaskAggregate.TerminalState.UNRESOLVED
            ),
        )

    deferred_worker = User.objects.create_user(
        username="operations-deferred",
        role=User.Role.WORKER,
        must_change_password=False,
    )
    deferred = assign_task(
        batch=batch,
        task=_published_task(asset, variant, "deferred"),
        worker=deferred_worker,
    )
    Assignment.objects.filter(pk=deferred.pk).update(queue_state=Assignment.QueueState.DEFERRED)
    BlockReport.objects.create(
        assignment=deferred,
        worker=deferred_worker,
        reason_schema_version="assignment-block-v1",
        reason_code=BlockReport.ReasonCode.UNABLE_TO_COMPLETE,
    )
    for kind, code in (
        (OperationalIssue.Kind.DRAFT_SAVE, "draft_conflict"),
        (OperationalIssue.Kind.ANNOTATION_STRUCTURE, "annotation_pair_invalid"),
        (OperationalIssue.Kind.MEDIA_DELIVERY, "image_unavailable"),
        (OperationalIssue.Kind.ACTIVITY_EVENT, "activity_event_conflict"),
    ):
        OperationalIssue.objects.create(
            assignment=deferred,
            kind=kind,
            error_code=code,
        )
    event_session_id = uuid4()
    active_lease_id = uuid4()
    event_wall_time = int((timezone.now() - timedelta(seconds=5)).timestamp() * 1000)
    for sequence_no, event_type, monotonic_ms in (
        (1, ActivityEvent.EventType.INTERACTION, 0),
        (2, ActivityEvent.EventType.HEARTBEAT, 5_000),
    ):
        ActivityEvent.objects.create(
            worker=resolved.worker,
            assignment=resolved.assignment,
            draft_cycle=resolved.draft_cycle,
            client_session_id=event_session_id,
            active_lease_id=active_lease_id,
            sequence_no=sequence_no,
            event_type=event_type,
            client_monotonic_ms=monotonic_ms,
            client_wall_time_ms=event_wall_time + monotonic_ms,
            visibility=ActivityEvent.Visibility.VISIBLE,
            focus=True,
            interaction_type=("annotation_2d_edit" if sequence_no == 1 else None),
            client_build_sha="test-build",
            active_time_rule_version="active-time-v1",
        )
    client = Client()
    client.force_login(administrator)
    batches_response = client.get("/api/admin/work-batches")
    response = client.get(f"/api/admin/work-batches/{batch.batch_id}/operations")

    assert batches_response.status_code == 200
    assert any(
        item["batch_id"] == str(batch.batch_id) for item in batches_response.json()["batches"]
    )
    assert response.status_code == 200
    assert response.json()["counts"] == {
        "active_workers": 3,
        "block_reports": 1,
        "deferred": 1,
        "event_errors": 1,
        "media_errors": 1,
        "pending": 1,
        "resolved": 1,
        "revisions": 3,
        "save_errors": 1,
        "structure_blocks": 2,
        "submitted": 3,
        "unresolved": 1,
        "verification_pending": 2,
    }
    assert response.json()["max_event_delay_ms"] >= 4_000
    assert response.json()["refreshed_at"].endswith("Z")
    assert response.json()["batch_id"] == str(batch.batch_id)
    assert pending.submission_assessments.count() == 0
    workers = {item["worker_id"]: item for item in response.json()["workers"]}
    assert workers[str(resolved.worker.worker_id)] == {
        "accepted": 1,
        "active_time_ms_by_rule": {"active-time-v1": 5_000},
        "block_reports": 0,
        "deferred": 0,
        "rework_requests": 0,
        "scope_observations": {"representation_oos": 1},
        "submitted": 1,
        "worker_id": str(resolved.worker.worker_id),
    }
    assert workers[str(deferred_worker.worker_id)]["deferred"] == 1
    assert workers[str(deferred_worker.worker_id)]["block_reports"] == 1
    assert "payment" not in str(response.json()).lower()


def test_operational_issue_ignores_foreign_or_missing_assignments() -> None:
    owner = User.objects.create_user(username="operations-owner", role=User.Role.WORKER)
    foreign = User.objects.create_user(username="operations-foreign", role=User.Role.WORKER)
    asset = Asset.objects.create(source_key="operations/owned")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key="cos://operations/owned.jpg",
        object_version="v1",
        content_sha256="c" * 64,
        content_length=10,
        content_crc64ecma="22",
        width=10,
        height=5,
        format=MediaVariant.Format.JPEG,
        role=MediaVariant.Role.COMPRESSED,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    assignment = assign_task(
        batch=create_work_batch(name="Issue ownership"),
        task=_published_task(asset, variant, "owned"),
        worker=owner,
    )

    assert (
        record_operational_issue(
            actor=foreign,
            assignment_id=assignment.assignment_id,
            kind=OperationalIssue.Kind.DRAFT_SAVE,
            error_code="draft_conflict",
        )
        is None
    )
    assert (
        record_operational_issue(
            actor=owner,
            assignment_id=uuid4(),
            kind=OperationalIssue.Kind.DRAFT_SAVE,
            error_code="draft_conflict",
        )
        is None
    )
    assert OperationalIssue.objects.count() == 0


def test_batch_operations_requires_admin_and_hides_missing_batch() -> None:
    worker = User.objects.create_user(username="operations-worker", role=User.Role.WORKER)
    administrator = User.objects.create_user(
        username="operations-admin-missing", role=User.Role.ADMIN
    )
    worker_client = Client()
    worker_client.force_login(worker)
    admin_client = Client()
    admin_client.force_login(administrator)
    path = f"/api/admin/work-batches/{uuid4()}/operations"

    assert worker_client.get(path).status_code == 403
    missing = admin_client.get(path)
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "resource_not_found"}}


def test_pap_aae_sc_002_pap_aae_sc_003_pap_aae_sc_004_metric_snapshot_is_frozen_and_reused() -> (
    None
):
    asset = Asset.objects.create(source_key="metric-snapshot/source")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key="cos://metric-snapshot/compressed.jpg",
        object_version="v1",
        content_sha256="d" * 64,
        content_length=100,
        content_crc64ecma="33",
        width=20,
        height=10,
        format=MediaVariant.Format.JPEG,
        role=MediaVariant.Role.COMPRESSED,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    batch = create_work_batch(name="Metric snapshot batch")
    revisions = [
        _submitted_assignment(
            batch=batch,
            task=_published_task(asset, variant, suffix),
            suffix=f"snapshot-{suffix}",
        )
        for suffix in ("support", "not-evaluable", "missing")
    ]
    for revision, structure_valid, digest in (
        (revisions[0], True, "e" * 64),
        (revisions[1], False, "f" * 64),
    ):
        SubmissionAssessment.objects.create(
            revision=revision,
            rule_version="submission-assessment-v1",
            input_sha256=submission_assessment_input_sha(revision),
            structure_valid=structure_valid,
            component_manifest={},
            assessment_sha256=digest,
        )

    snapshot, created, reused = request_metric_snapshot(batch_id=batch.batch_id)
    replayed, replay_created, replay_reused = request_metric_snapshot(batch_id=batch.batch_id)

    assert created is True
    assert reused is False
    assert replay_created is False
    assert replay_reused is False
    assert replayed.snapshot_id == snapshot.snapshot_id
    assert snapshot.status == MetricSnapshot.Status.PENDING
    assert {item["revision_id"] for item in snapshot.input_manifest["inputs"]} == {
        str(revision.revision_id) for revision in revisions
    }
    assert all("state" not in item for item in snapshot.input_manifest["inputs"])
    assert "current_draft" not in str(snapshot.input_manifest).lower()
    assert snapshot.input_manifest["platform_release_id"] == "panorama-platform-v1"
    assert snapshot.input_manifest["assessment_rule_version"] == "submission-assessment-v1"
    assert snapshot.input_manifest["inputs"][0]["media"][0]["object_version"] == "v1"

    completed = process_next_metric_snapshot(worker_id="metric-snapshot-worker")

    assert completed is not None
    assert completed.snapshot_id == snapshot.snapshot_id
    assert completed.status == MetricSnapshot.Status.SUCCEEDED
    assert completed.result_manifest == {
        "missing": 1,
        "not_evaluable": 1,
        "support": 1,
        "total_revisions": 3,
    }
    reused_snapshot, reused_created, reused_completed = request_metric_snapshot(
        batch_id=batch.batch_id
    )
    assert reused_snapshot.snapshot_id == completed.snapshot_id
    assert reused_created is False
    assert reused_completed is True
    frozen_manifest = completed.input_manifest
    frozen_result = completed.result_manifest

    _submitted_assignment(
        batch=batch,
        task=_published_task(asset, variant, "after-cutoff"),
        suffix="snapshot-after-cutoff",
    )
    newer, newer_created, newer_reused = request_metric_snapshot(batch_id=batch.batch_id)
    completed.refresh_from_db()

    assert newer_created is True
    assert newer_reused is False
    assert newer.snapshot_id != completed.snapshot_id
    assert len(newer.input_manifest["inputs"]) == 4
    assert completed.input_manifest == frozen_manifest
    assert completed.result_manifest == frozen_result
    with pytest.raises(DatabaseError), transaction.atomic():
        MetricSnapshot.objects.filter(pk=completed.pk).update(support=99)
    with pytest.raises(DatabaseError), transaction.atomic():
        MetricSnapshot.objects.filter(pk=completed.pk).delete()


def test_admin_requests_metric_snapshot_job_without_running_it_inline() -> None:
    batch = create_work_batch(name="Metric API batch")
    administrator = User.objects.create_user(username="metric-api-admin", role=User.Role.ADMIN)
    client = Client()
    client.force_login(administrator)
    path = f"/api/admin/work-batches/{batch.batch_id}/metric-snapshots"

    created = client.post(path, data="{}", content_type="application/json")
    pending = client.post(path, data="{}", content_type="application/json")
    process_next_metric_snapshot(worker_id="metric-api-worker")
    replayed = client.post(path, data="{}", content_type="application/json")

    assert created.status_code == 202
    assert created.json()["status"] == MetricSnapshot.Status.PENDING
    assert pending.status_code == 202
    assert pending.json()["snapshot_id"] == created.json()["snapshot_id"]
    assert pending.json()["reused_from_snapshot_id"] is None
    assert replayed.status_code == 200
    assert replayed.json()["snapshot_id"] == created.json()["snapshot_id"]
    assert replayed.json()["reused_from_snapshot_id"] == created.json()["snapshot_id"]
    assert MetricSnapshot.objects.count() == 1
    events = list(AuditEvent.objects.filter(action="metric_snapshot.requested"))
    assert all(event.target_type == "metric_snapshot" for event in events)
    assert {event.target_id for event in events} == {created.json()["snapshot_id"]}
    assert [event.reason for event in events].count("reused_existing_input") == 1
    assert all(event.correlation_id is not None for event in events)


def test_existing_batch_state_actions_use_real_audit_targets() -> None:
    batch = create_work_batch(name="Audited batch")
    administrator = User.objects.create_user(username="batch-audit-admin", role=User.Role.ADMIN)
    client = Client()
    client.force_login(administrator)

    assert client.post(f"/api/admin/work-batches/{batch.batch_id}/freeze").status_code == 200
    assert client.post(f"/api/admin/work-batches/{batch.batch_id}/reopen").status_code == 200

    events = list(
        AuditEvent.objects.filter(target_type="work_batch", target_id=str(batch.batch_id)).order_by(
            "created_at"
        )
    )
    assert [event.action for event in events] == ["batch.frozen", "batch.reopened"]
    assert {event.reason for event in events} == {"administrator_request"}
    assert all(event.correlation_id for event in events)


def test_metric_snapshot_management_command_runs_the_background_job() -> None:
    batch = create_work_batch(name="Metric command batch")
    snapshot, created, reused = request_metric_snapshot(batch_id=batch.batch_id)
    output = StringIO()

    call_command(
        "process_metric_snapshots",
        max_jobs=1,
        worker_id="metric-command-worker",
        stdout=output,
    )

    snapshot.refresh_from_db()
    assert created is True
    assert reused is False
    assert snapshot.status == MetricSnapshot.Status.SUCCEEDED
    assert snapshot.result_manifest == {
        "missing": 0,
        "not_evaluable": 0,
        "support": 0,
        "total_revisions": 0,
    }
    assert output.getvalue().strip() == "processed=1 failures=0"


def test_pap_aae_sc_009_pap_aae_sc_010_batch_export_freezes_identity_and_versions() -> None:
    _administrator, batch, consensus, review, revision, selection, variant = _export_fixture()

    snapshot, created, reused = request_batch_export(batch_id=batch.batch_id)
    assert set(snapshot.input_manifest) == {
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
    assert "state" not in repr(snapshot.input_manifest).lower()
    assert "media_variants" not in repr(snapshot.input_manifest).lower()
    later_revision = _submitted_assignment(
        batch=batch,
        task=_published_task(variant.asset, variant, "export-later"),
        suffix="export-later",
    )
    completed = process_next_batch_export(worker_id="export-worker")

    assert created is True
    assert reused is False
    assert completed == snapshot
    assert completed is not None and completed.status == BatchExportSnapshot.Status.SUCCEEDED
    manifest = completed.export_manifest
    assert set(manifest) == {
        "artifact_type",
        "authority",
        "batch",
        "cutoff",
        "exported_at",
        "is_dataset_release",
        "is_final_gold",
        "platform_release_id",
        "rule_version",
        "tasks",
        "views",
    }
    assert manifest["artifact_type"] == "batch_export_snapshot"
    assert manifest["authority"] == "batch_snapshot"
    assert manifest["is_final_gold"] is False
    assert manifest["is_dataset_release"] is False
    assert manifest["batch"]["batch_id"] == str(batch.batch_id)
    assert completed.export_sha256 == json_sha256(manifest)

    task = manifest["tasks"][0]
    assert task["task_id"] == str(revision.task_id)
    assert task["asset_id"] == str(variant.asset_id)
    assert "prediction_artifact" not in task
    assert "geometry_engine_version" not in task
    assert "assist_version" not in task
    assert task["meta_schema_version"] == revision.meta_schema_version
    assert task["meta_copy_version"] == revision.meta_copy_version
    assert task["media_variants"] == [
        {
            "asset_id": str(variant.asset_id),
            "content_crc64ecma": "321",
            "content_length": 321,
            "content_sha256": "e" * 64,
            "coordinate_mapping": "normalized_identity",
            "format": "jpeg",
            "height": 10,
            "media_variant_id": str(variant.media_variant_id),
            "object_version": "exports-v1",
            "role": "compressed",
            "width": 20,
        }
    ]
    assignment = task["assignments"][0]
    exported_revision = assignment["revisions"][0]
    assert assignment["worker_id"] == str(revision.worker.worker_id)
    assert exported_revision["revision_id"] == str(revision.revision_id)
    assert exported_revision["state"] == revision.state
    assert exported_revision["state_sha"] == revision.state_sha
    assert exported_revision["platform_release_id"] == "panorama-platform-v1"
    assert exported_revision["client_build_sha"] == "test-client-build"
    assert exported_revision["viewer_version"] == "annotation-viewer-v1"
    assert exported_revision["interaction_contract_version"] == "annotation-interaction-v1"
    assert "geometry_engine_version" not in exported_revision
    assert exported_revision["reviews"][0]["review_id"] == str(review.review_id)
    assert task["consensus_artifacts"][0]["artifact_id"] == str(consensus.artifact_id)
    assert task["consensus_artifacts"][0]["geometry_medoid_revision_id"] == str(
        revision.revision_id
    )
    assert task["delivery_selections"][0]["selection_id"] == str(selection.selection_id)
    assert manifest["views"] == {
        "latest_submitted": [
            {
                "assignment_id": str(revision.assignment_id),
                "revision_id": str(revision.revision_id),
                "task_id": str(revision.task_id),
            }
        ],
        "selected_delivery": [
            {
                "selection_id": str(selection.selection_id),
                "source_id": str(revision.revision_id),
                "source_type": "worker_revision",
                "task_id": str(revision.task_id),
            }
        ],
    }
    assert "current_draft" not in repr(manifest).lower()
    assert "draft_id" not in repr(manifest).lower()
    assert str(later_revision.revision_id) not in repr(manifest)

    frozen = completed.export_manifest
    with pytest.raises(DatabaseError), transaction.atomic():
        BatchExportSnapshot.objects.filter(pk=completed.pk).update(export_manifest={})
    with pytest.raises(DatabaseError), transaction.atomic():
        BatchExportSnapshot.objects.filter(pk=completed.pk).delete()
    completed.refresh_from_db()
    assert completed.export_manifest == frozen


def test_batch_export_api_is_async_audited_and_reusable() -> None:
    administrator, batch, _consensus, _review, revision, _selection, _variant = _export_fixture()
    client = Client()
    client.force_login(administrator)
    path = f"/api/admin/work-batches/{batch.batch_id}/exports"

    created = client.post(path, data="{}", content_type="application/json")
    assert created.status_code == 202
    assert created.json()["status"] == BatchExportSnapshot.Status.PENDING
    process_next_batch_export(worker_id="export-api-worker")
    replayed = client.post(path, data="{}", content_type="application/json")
    fetched = client.get(f"/api/admin/batch-exports/{created.json()['export_id']}")

    assert replayed.status_code == 200
    assert replayed.json()["reused_from_export_id"] == created.json()["export_id"]
    assert fetched.status_code == 200
    assert fetched.json()["manifest"]["artifact_type"] == "batch_export_snapshot"
    event = AuditEvent.objects.filter(action="batch_export.requested").latest("created_at")
    assert event.target_type == "batch_export_snapshot"
    assert event.target_id == created.json()["export_id"]
    assert event.correlation_id is not None

    client.force_login(revision.worker)
    assert client.post(path, data="{}", content_type="application/json").status_code == 403
    assert client.get(f"/api/admin/batch-exports/{created.json()['export_id']}").status_code == 403


def test_batch_export_worker_rejects_a_tampered_input_manifest() -> None:
    _administrator, batch, _consensus, _review, _revision, _selection, _variant = _export_fixture()
    snapshot, _created, _reused = request_batch_export(batch_id=batch.batch_id)
    BatchExportSnapshot.objects.filter(pk=snapshot.pk).update(input_manifest={"tampered": True})

    failed = process_next_batch_export(worker_id="tampered-export-worker")

    assert failed is not None and failed.status == BatchExportSnapshot.Status.FAILED
    assert failed.last_error_code == "batch_export_manifest_mismatch"
    assert failed.export_manifest == {}

    retried, created, reused = request_batch_export(batch_id=batch.batch_id)
    recovered = process_next_batch_export(worker_id="recovered-export-worker")

    assert retried == snapshot
    assert created is False
    assert reused is False
    assert recovered is not None and recovered.status == BatchExportSnapshot.Status.SUCCEEDED
