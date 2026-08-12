from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
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
from work.jobs import (
    analysis_job_metrics,
    eligible_task_input_manifest,
    process_next_analysis_job,
    request_revision_audit,
)
from work.models import (
    AnalysisJob,
    AnnotationRevision,
    Assignment,
    AssignmentProposal,
    AuditArtifact,
    CurrentDraft,
    OperationalIssue,
    SubmissionAssessment,
    Task,
    TaskAggregate,
    TaskConsensusArtifact,
    TaskDeliverySelection,
    TaskEligibilityArtifact,
    WorkBatch,
)
from work.services import (
    adjudicate_task,
    assign_task,
    cancel_task,
    create_task_draft,
    create_work_batch,
    freeze_work_batch,
    open_owned_assignment,
    publish_task,
    save_owned_current_draft,
    start_owned_revision_cycle,
    submit_owned_current_draft,
)

pytestmark = pytest.mark.django_db


def submitted_revision(
    suffix: str,
    *,
    batch: WorkBatch | None = None,
    state: dict[str, object] | None = None,
    task: Task | None = None,
) -> tuple[AnnotationRevision, Assignment]:
    worker = User.objects.create_user(
        username=f"analysis-{suffix}",
        role=User.Role.WORKER,
        must_change_password=False,
    )
    DataNoticeAcceptance.objects.create(worker=worker, notice_version=CURRENT_DATA_NOTICE_VERSION)
    if task is None:
        asset = Asset.objects.create(source_key=f"analysis/{suffix}")
        variant = MediaVariant.objects.create(
            asset=asset,
            source_key=f"cos://analysis/{suffix}.jpg",
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
        batch=batch or create_work_batch(name=f"Analysis {suffix}"),
        task=task,
        worker=worker,
    )
    tab_id = uuid4()
    workspace = ActiveWorkspace.objects.create(
        worker=worker,
        session_key=f"analysis-{suffix}-session",
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
        state=state
        or {
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
    return revision, assignment


def canonical_geometry_state(
    pair_us: list[float], *, portal_u: float | None = None
) -> dict[str, object]:
    pairs = [
        {
            "bottom": {"point_id": str(uuid4()), "u": u, "v": 0.8},
            "order_index": index,
            "pair_id": str(uuid4()),
            "top": {"point_id": str(uuid4()), "u": u, "v": 0.2},
        }
        for index, u in enumerate(pair_us)
    ]
    portals = []
    if portal_u is not None:
        portals.append(
            {
                "evidence_status": "direct_visible",
                "geometry": {
                    "bottom_left": {"u": portal_u, "v": 0.75},
                    "bottom_right": {"u": (portal_u + 0.05) % 1, "v": 0.75},
                    "top_left": {"u": portal_u, "v": 0.25},
                    "top_right": {"u": (portal_u + 0.05) % 1, "v": 0.25},
                },
                "host_edge_ref": None,
                "kind": "door",
                "portal_id": str(uuid4()),
            }
        )
    return {
        "difficulty": ["trivial"],
        "geometry_attempt_reason_text": "",
        "geometry_attempt_status": "best_effort_complete",
        "pairs": pairs,
        "portals": portals,
        "seam_anchor_pair_id": pairs[0]["pair_id"],
        "scope_reason_codes": [],
        "scope_reason_text": "",
        "worker_scope_observation": "annotatable",
    }


def test_pap_acr_sc_001_failed_analysis_keeps_revision_pending_and_retries() -> None:
    revision, assignment = submitted_revision("retry")
    job = AnalysisJob.objects.get(revision=revision)
    assert job.status == AnalysisJob.Status.PENDING
    assert AnalysisJob.objects.count() == 1

    with patch("work.jobs._assessment_manifest", side_effect=RuntimeError("temporary")):
        failed = process_next_analysis_job(worker_id="worker-one")

    assert failed is not None and failed.status == AnalysisJob.Status.FAILED
    assert AnnotationRevision.objects.filter(pk=revision.pk).exists()
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.SUBMITTED
    assert SubmissionAssessment.objects.count() == 0
    assert (
        eligible_task_input_manifest(
            task_id=revision.task_id,
            batch_id=revision.assignment.batch_id,
        )["inputs"]
        == []
    )
    assert analysis_job_metrics() == {"attempts": 1, "backlog": 1, "failures": 1}

    AnalysisJob.objects.filter(pk=job.pk).update(available_at=timezone.now())
    completed = process_next_analysis_job(worker_id="worker-two")

    assert completed is not None and completed.status == AnalysisJob.Status.SUCCEEDED
    assessment = SubmissionAssessment.objects.get(revision=revision)
    aggregate = TaskAggregate.objects.get(task=revision.task, batch=revision.assignment.batch)
    assert completed.output_manifest == {
        "assessment_id": str(assessment.assessment_id),
        "assessment_sha256": assessment.assessment_sha256,
        "eligible_input_manifest": eligible_task_input_manifest(
            task_id=revision.task_id,
            batch_id=revision.assignment.batch_id,
        ),
        "geometry_aggregation": {
            "cluster_revision_ids": [],
            "margin": 0,
            "medoid_revision_id": None,
            "primary_support": 0,
            "secondary_support": 0,
            "state": "not_evaluable",
        },
        "portal_aggregation": {
            "cluster_revision_ids": [str(revision.revision_id)],
            "margin": 1,
            "medoid_revision_id": None,
            "primary_support": 1,
            "secondary_support": 0,
            "state": "needs_more",
        },
        "scope_aggregation": {
            "reason_code": "insufficient_evidence",
            "state": "needs_more",
            "support": 1,
        },
        "task_aggregate_id": str(aggregate.aggregate_id),
        "terminal_state": "needs_more",
    }
    administrator = User.objects.create_user(username="assessment-admin", role=User.Role.ADMIN)
    client = Client()
    client.force_login(administrator)
    inspected = client.get(f"/api/admin/revisions/{revision.revision_id}")
    assert inspected.json()["verification_status"] == "assessment_complete"


def test_pap_acr_sc_002_reprocessing_same_input_reuses_one_assessment() -> None:
    revision, _assignment = submitted_revision("idempotent")
    first = process_next_analysis_job(worker_id="worker-one")
    assert first is not None and first.status == AnalysisJob.Status.SUCCEEDED
    assessment_id = SubmissionAssessment.objects.get(revision=revision).assessment_id
    AnalysisJob.objects.filter(pk=first.pk).update(
        available_at=timezone.now(),
        status=AnalysisJob.Status.FAILED,
    )

    second = process_next_analysis_job(worker_id="worker-two")

    assert second is not None and second.status == AnalysisJob.Status.SUCCEEDED
    assert SubmissionAssessment.objects.get(revision=revision).assessment_id == assessment_id
    assert SubmissionAssessment.objects.count() == 1
    assert AnalysisJob.objects.count() == 1


def test_task_10_3_structurally_invalid_assessment_is_not_eligible() -> None:
    revision, _assignment = submitted_revision("structurally-invalid")
    with patch(
        "work.jobs._assessment_manifest",
        return_value={
            "components": {"evidence": False, "geometry": False, "portal": False, "scope": False},
            "structure_valid": False,
        },
    ):
        completed = process_next_analysis_job(worker_id="invalid-assessment")

    assert completed is not None and completed.status == AnalysisJob.Status.SUCCEEDED
    assert not SubmissionAssessment.objects.get(revision=revision).structure_valid
    assert (
        eligible_task_input_manifest(
            task_id=revision.task_id,
            batch_id=revision.assignment.batch_id,
        )["inputs"]
        == []
    )


def test_task_10_4_submission_assessment_is_database_immutable() -> None:
    revision, _assignment = submitted_revision("immutable-assessment")
    process_next_analysis_job(worker_id="immutable-assessment")
    assessment = SubmissionAssessment.objects.get(revision=revision)

    with pytest.raises(DatabaseError), transaction.atomic():
        SubmissionAssessment.objects.filter(pk=assessment.pk).update(structure_valid=False)
    with pytest.raises(DatabaseError), transaction.atomic():
        SubmissionAssessment.objects.filter(pk=assessment.pk).delete()


def test_task_10_1_revision_and_outbox_are_one_transaction() -> None:
    with (
        patch("work.services.enqueue_submission_assessment", side_effect=RuntimeError("outbox")),
        pytest.raises(RuntimeError, match="outbox"),
    ):
        submitted_revision("atomic")

    assert AnnotationRevision.objects.count() == 0
    assert AnalysisJob.objects.count() == 0
    assignment = Assignment.objects.get()
    assert assignment.work_state == Assignment.WorkState.IN_PROGRESS


def test_task_10_2_admin_reads_incremental_job_metrics_without_running_analysis() -> None:
    submitted_revision("metrics")
    administrator = User.objects.create_user(username="metrics-admin", role=User.Role.ADMIN)
    client = Client()
    client.force_login(administrator)

    response = client.get("/api/admin/analysis-jobs/metrics")

    assert response.status_code == 200
    assert response.json() == {"attempts": 0, "backlog": 1, "failures": 0}
    assert SubmissionAssessment.objects.count() == 0


def test_task_10_2_management_command_runs_the_independent_worker() -> None:
    revision, _assignment = submitted_revision("command")
    output = StringIO()

    call_command(
        "process_analysis_jobs",
        max_jobs=1,
        worker_id="command-worker",
        stdout=output,
    )

    assert "processed=1 failures=0" in output.getvalue()
    assert SubmissionAssessment.objects.filter(revision=revision).count() == 1


def test_pap_acr_sc_010_pap_acr_sc_024_latest_worker_revision_keeps_components() -> None:
    first, assignment = submitted_revision("eligible-input")
    process_next_analysis_job(worker_id="worker-one")
    workspace = ActiveWorkspace.objects.get(worker=assignment.worker)
    workspace_args = {
        "session_key": workspace.session_key,
        "session_token": str(workspace.token),
        "tab_id": workspace.tab_id,
    }
    start_owned_revision_cycle(
        actor=assignment.worker,
        assignment_id=assignment.assignment_id,
        **workspace_args,
    )
    pair_id = str(uuid4())
    draft = save_owned_current_draft(
        actor=assignment.worker,
        assignment_id=assignment.assignment_id,
        expected_draft_version=0,
        state={
            "difficulty": ["occlusion"],
            "geometry_attempt_reason_text": "",
            "geometry_attempt_status": "partial",
            "pairs": [
                {
                    "bottom": {"point_id": str(uuid4()), "u": 0.25, "v": 0.8},
                    "order_index": 0,
                    "pair_id": pair_id,
                    "top": {"point_id": str(uuid4()), "u": 0.25, "v": 0.2},
                }
            ],
            "portals": [
                {
                    "evidence_status": "direct_visible",
                    "geometry": {
                        "bottom_left": {"u": 0.3, "v": 0.75},
                        "bottom_right": {"u": 0.45, "v": 0.75},
                        "top_left": {"u": 0.3, "v": 0.35},
                        "top_right": {"u": 0.45, "v": 0.35},
                    },
                    "host_edge_ref": pair_id,
                    "kind": "door",
                    "portal_id": str(uuid4()),
                }
            ],
            "seam_anchor_pair_id": pair_id,
            "scope_reason_codes": ["severe_image_artifact"],
            "scope_reason_text": "",
            "worker_scope_observation": "representation_oos",
        },
        **workspace_args,
    )
    second, created = submit_owned_current_draft(
        actor=assignment.worker,
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
    assert (
        eligible_task_input_manifest(
            task_id=assignment.task_id,
            batch_id=assignment.batch_id,
        )["inputs"]
        == []
    )

    completed = process_next_analysis_job(worker_id="worker-two")
    manifest = eligible_task_input_manifest(
        task_id=assignment.task_id,
        batch_id=assignment.batch_id,
    )

    assert completed is not None
    assert [item["revision_id"] for item in manifest["inputs"]] == [str(second.revision_id)]
    assert manifest["inputs"][0]["eligible_components"] == [
        "evidence",
        "geometry",
        "portal",
        "scope",
    ]
    assert str(first.revision_id) not in str(manifest)
    assert completed.output_manifest["eligible_input_manifest"] == manifest


def test_pap_acr_sc_022_scope_policy_creates_a_frozen_eligibility_artifact() -> None:
    batch = create_work_batch(
        name="Scope policy batch",
        scope_policy={
            "auto_close_reason_codes": ["insufficient_evidence"],
            "maximum_support": 5,
            "minimum_support": 3,
            "version": "scope-policy-v1",
        },
    )
    first, _assignment = submitted_revision("scope-policy-one", batch=batch)
    second, _assignment = submitted_revision(
        "scope-policy-two",
        batch=batch,
        task=first.task,
    )
    third, _assignment = submitted_revision(
        "scope-policy-three",
        batch=batch,
        task=first.task,
    )

    first_completed = process_next_analysis_job(worker_id="scope-one")
    assert first_completed is not None
    aggregate = TaskAggregate.objects.get(task=first.task, batch=batch)
    assert aggregate.scope_state == TaskAggregate.ScopeState.NEEDS_MORE
    assert aggregate.scope_support == 1
    process_next_analysis_job(worker_id="scope-two")
    completed = process_next_analysis_job(worker_id="scope-three")

    assert completed is not None
    artifact = TaskEligibilityArtifact.objects.get(task=first.task, batch=batch)
    aggregate.refresh_from_db()
    assert aggregate.scope_state == TaskAggregate.ScopeState.RESOLVED_OOS
    assert aggregate.input_sha256 == artifact.input_sha256
    assert artifact.outcome == "representation_oos"
    assert artifact.reason_code == "insufficient_evidence"
    assert artifact.policy_version == "scope-policy-v1"
    assert {item["revision_id"] for item in artifact.input_manifest["inputs"]} == {
        str(first.revision_id),
        str(second.revision_id),
        str(third.revision_id),
    }
    assert completed.output_manifest["scope_aggregation"] == {
        "reason_code": "insufficient_evidence",
        "state": "resolved_oos",
        "support": 3,
    }
    assert completed.output_manifest["eligibility_artifact_id"] == str(artifact.artifact_id)
    assert AssignmentProposal.objects.count() == 0

    freeze_work_batch(batch_id=batch.batch_id)
    with pytest.raises(DatabaseError), transaction.atomic():
        WorkBatch.objects.filter(pk=batch.pk).update(
            scope_policy={
                **batch.scope_policy,
                "auto_close_reason_codes": [],
            }
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        WorkBatch.objects.filter(pk=batch.pk).update(
            consensus_policy={**batch.consensus_policy, "k_max": 4}
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        TaskEligibilityArtifact.objects.filter(pk=artifact.pk).delete()


def test_task_10_9_needs_more_proposes_one_addition_without_assigning() -> None:
    batch = create_work_batch(name="Consensus proposal batch")
    first, _assignment = submitted_revision("proposal-one", batch=batch)
    second, _assignment = submitted_revision("proposal-two", batch=batch, task=first.task)
    third, _assignment = submitted_revision("proposal-three", batch=batch, task=first.task)

    process_next_analysis_job(worker_id="proposal-one")
    process_next_analysis_job(worker_id="proposal-two")
    completed = process_next_analysis_job(worker_id="proposal-three")

    proposal = AssignmentProposal.objects.get(batch=batch, task=first.task)
    assert proposal.purpose == AssignmentProposal.Purpose.CONSENSUS_ADDITION
    assert proposal.requested_count == 1
    assert proposal.policy_version == "consensus-policy-v2"
    assert {item["revision_id"] for item in proposal.input_manifest["inputs"]} == {
        str(first.revision_id),
        str(second.revision_id),
        str(third.revision_id),
    }
    assert Assignment.objects.filter(batch=batch, task=first.task).count() == 3
    assert completed is not None
    assert completed.output_manifest["assignment_proposal_id"] == str(proposal.proposal_id)

    AnalysisJob.objects.filter(revision=third).update(
        available_at=timezone.now(),
        status=AnalysisJob.Status.FAILED,
    )
    retried = process_next_analysis_job(worker_id="proposal-retry")
    assert retried is not None
    assert AssignmentProposal.objects.filter(batch=batch, task=first.task).count() == 1


def test_pap_acr_sc_006_and_008_component_consensus_freezes_real_medoids() -> None:
    batch = create_work_batch(name="Component consensus batch")
    first, _assignment = submitted_revision(
        "component-one",
        batch=batch,
        state=canonical_geometry_state([0.99, 0.25, 0.55], portal_u=0.2),
    )
    second, _assignment = submitted_revision(
        "component-two",
        batch=batch,
        task=first.task,
        state=canonical_geometry_state([0.251, 0.551, 0.001], portal_u=0.21),
    )
    third, _assignment = submitted_revision(
        "component-three",
        batch=batch,
        task=first.task,
        state=canonical_geometry_state([0.549, 0.249, 0.989], portal_u=0.214),
    )

    process_next_analysis_job(worker_id="component-one")
    process_next_analysis_job(worker_id="component-two")
    completed = process_next_analysis_job(worker_id="component-three")

    assert completed is not None
    aggregate = TaskAggregate.objects.get(batch=batch, task=first.task)
    artifact = TaskConsensusArtifact.objects.get(batch=batch, task=first.task)
    revision_ids = {first.revision_id, second.revision_id, third.revision_id}
    assert aggregate.geometry_state == "resolved"
    assert aggregate.portal_state == "resolved"
    assert aggregate.terminal_state == "resolved"
    assert aggregate.geometry_primary_support == 3
    assert aggregate.portal_primary_support == 3
    assert artifact.geometry_medoid_revision_id in revision_ids
    assert artifact.portal_medoid_revision_id in revision_ids
    assert artifact.component_manifest["geometry"]["medoid_revision_id"] == str(
        artifact.geometry_medoid_revision_id
    )
    assert artifact.component_manifest["portal"]["medoid_revision_id"] == str(
        artifact.portal_medoid_revision_id
    )
    assert artifact.geometry_medoid_revision.state in [first.state, second.state, third.state]
    assert artifact.portal_medoid_revision.state in [first.state, second.state, third.state]
    assert completed.output_manifest["task_consensus_artifact_id"] == str(artifact.artifact_id)
    assert AssignmentProposal.objects.count() == 0

    frozen_hash = artifact.input_sha256
    # PAP-ACR-SC-009: a later adjudication must not rewrite the consensus artifact.
    administrator = User.objects.create_user(
        username="component-consensus-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    adjudicate_task(
        actor=administrator,
        task_id=first.task_id,
        state=first.state,
        source_revision_ids=[first.revision_id, second.revision_id, third.revision_id],
        reason="Later administrator decision.",
    )
    artifact.refresh_from_db()
    assert artifact.input_sha256 == frozen_hash
    assert TaskConsensusArtifact.objects.count() == 1
    with pytest.raises(DatabaseError), transaction.atomic():
        TaskConsensusArtifact.objects.filter(pk=artifact.pk).update(input_sha256="f" * 64)
    with pytest.raises(DatabaseError), transaction.atomic():
        TaskConsensusArtifact.objects.filter(pk=artifact.pk).delete()


def test_pap_aae_sc_003_unresolved_consensus_appears_in_filterable_review_queue() -> None:
    batch = create_work_batch(name="Unresolved review queue batch")
    first, _assignment = submitted_revision(
        "review-queue-one",
        batch=batch,
        state=canonical_geometry_state([0.1, 0.4, 0.7]),
    )
    revisions = [first]
    for suffix, pair_us in (
        ("review-queue-two", [0.105, 0.405, 0.705]),
        ("review-queue-three", [0.11, 0.41, 0.71]),
        ("review-queue-four", [0.2, 0.5, 0.8]),
        ("review-queue-five", [0.205, 0.505, 0.805]),
    ):
        revision, _assignment = submitted_revision(
            suffix,
            batch=batch,
            task=first.task,
            state=canonical_geometry_state(pair_us),
        )
        revisions.append(revision)

    completed = None
    for sequence in range(len(revisions)):
        completed = process_next_analysis_job(worker_id=f"review-queue-worker-{sequence}")
    aggregate = TaskAggregate.objects.get(batch=batch, task=first.task)
    administrator = User.objects.create_user(
        username="review-queue-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    client = Client()
    client.force_login(administrator)

    response = client.get(
        f"/api/admin/work-batches/{batch.batch_id}/review-queue?reason_code=geometry_multimodal"
    )
    excluded = client.get(
        f"/api/admin/work-batches/{batch.batch_id}/review-queue?reason_code=portal_multimodal"
    )

    assert completed is not None and completed.status == AnalysisJob.Status.SUCCEEDED
    assert aggregate.terminal_state == TaskAggregate.TerminalState.UNRESOLVED
    assert response.status_code == 200
    assert response.json()["batch_id"] == str(batch.batch_id)
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert item["task_id"] == str(first.task_id)
    assert item["input_sha256"] == aggregate.input_sha256
    assert set(item["input_revision_ids"]) == {str(revision.revision_id) for revision in revisions}
    assert item["conflict_summary"] == {
        "geometry_state": "unresolved",
        "portal_state": "resolved",
        "reason_codes": ["geometry_multimodal"],
        "scope_state": "unresolved",
    }
    assert item["rule_versions"] == {
        "consensus_policy": "consensus-policy-v2",
        "scope_policy": "scope-policy-v1",
    }
    assert excluded.status_code == 200
    assert excluded.json()["items"] == []
    worker_client = Client()
    worker_client.force_login(first.worker)
    assert (
        worker_client.get(f"/api/admin/work-batches/{batch.batch_id}/review-queue").status_code
        == 403
    )


def test_scope_geometry_conflict_appears_in_review_queue_without_exposing_state() -> None:
    def conflicting_state() -> dict[str, object]:
        state = canonical_geometry_state([0.1, 0.4, 0.7])
        state["scope_reason_codes"] = ["insufficient_evidence"]
        state["worker_scope_observation"] = "representation_oos"
        return state

    batch = create_work_batch(name="Scope conflict review queue batch")
    first, _assignment = submitted_revision(
        "scope-review-queue-one",
        batch=batch,
        state=conflicting_state(),
    )
    revisions = [first]
    for suffix in (
        "scope-review-queue-two",
        "scope-review-queue-three",
        "scope-review-queue-four",
        "scope-review-queue-five",
    ):
        revision, _assignment = submitted_revision(
            suffix,
            batch=batch,
            task=first.task,
            state=conflicting_state(),
        )
        revisions.append(revision)
    for sequence in range(len(revisions)):
        process_next_analysis_job(worker_id=f"scope-review-queue-worker-{sequence}")

    administrator = User.objects.create_user(
        username="scope-review-queue-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    client = Client()
    client.force_login(administrator)
    response = client.get(
        f"/api/admin/work-batches/{batch.batch_id}/review-queue"
        "?reason_code=scope_structure_conflict"
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert item["task_id"] == str(first.task_id)
    assert item["input_revision_ids"] == [str(revision.revision_id) for revision in revisions]
    assert set(item) == {
        "conflict_summary",
        "input_revision_ids",
        "input_sha256",
        "queue_type",
        "rule_versions",
        "task_id",
        "updated_at",
    }
    assert item["conflict_summary"] == {
        "geometry_state": "resolved",
        "portal_state": "resolved",
        "reason_codes": ["scope_structure_conflict", "scope_unresolved"],
        "scope_state": "unresolved",
    }
    assert item["queue_type"] == "consensus_unresolved"


def test_audit_artifact_finding_appears_in_review_queue() -> None:
    state = canonical_geometry_state([0.1, 0.4, 0.7], portal_u=0.2)
    state["scope_reason_codes"] = ["insufficient_evidence"]
    state["worker_scope_observation"] = "representation_oos"
    revision, assignment = submitted_revision("audit-review-queue", state=state)
    request_revision_audit(revision_id=revision.revision_id, audit_type="scope_portal")
    while process_next_analysis_job(worker_id="audit-review-queue-worker") is not None:
        pass

    artifact = AuditArtifact.objects.get(revision=revision, audit_type="scope_portal")
    administrator = User.objects.create_user(
        username="audit-review-queue-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    client = Client()
    client.force_login(administrator)
    response = client.get(
        f"/api/admin/work-batches/{assignment.batch_id}/review-queue"
        "?reason_code=scope_portal_conflict"
    )

    assert artifact.requires_review is True
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "conflict_summary": {
                "audit_type": "scope_portal",
                "reason_codes": ["scope_portal_conflict"],
            },
            "input_revision_ids": [str(revision.revision_id)],
            "input_sha256": artifact.input_sha256,
            "queue_type": "audit_finding",
            "rule_versions": {
                "audit_code": "audit-code-v1",
                "audit_rule": "audit-rule-v1",
            },
            "task_id": str(revision.task_id),
            "updated_at": artifact.created_at.isoformat().replace("+00:00", "Z"),
        }
    ]


def test_operational_issue_appears_in_review_queue_and_filters_by_error_code() -> None:
    revision, assignment = submitted_revision("operational-review-queue")
    issue = OperationalIssue.objects.create(
        assignment=assignment,
        kind=OperationalIssue.Kind.MEDIA_DELIVERY,
        error_code="image_unavailable",
    )
    administrator = User.objects.create_user(
        username="operational-review-queue-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    client = Client()
    client.force_login(administrator)

    response = client.get(
        f"/api/admin/work-batches/{assignment.batch_id}/review-queue?reason_code=image_unavailable"
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "conflict_summary": {
                "error_code": "image_unavailable",
                "issue_kind": "media_delivery",
                "reason_codes": ["image_unavailable"],
            },
            "input_revision_ids": [],
            "input_sha256": None,
            "queue_id": str(issue.issue_id),
            "queue_type": "operational_issue",
            "rule_versions": {},
            "task_id": str(revision.task_id),
            "updated_at": issue.created_at.isoformat().replace("+00:00", "Z"),
        }
    ]


def test_task_10_9_does_not_propose_while_an_assignment_is_outstanding() -> None:
    batch = create_work_batch(name="Outstanding assignment batch")
    first, _assignment = submitted_revision("outstanding-one", batch=batch)
    submitted_revision("outstanding-two", batch=batch, task=first.task)
    submitted_revision("outstanding-three", batch=batch, task=first.task)
    fourth_worker = User.objects.create_user(
        username="analysis-outstanding-four",
        role=User.Role.WORKER,
        must_change_password=False,
    )
    assign_task(batch=batch, task=first.task, worker=fourth_worker)

    process_next_analysis_job(worker_id="outstanding-one")
    process_next_analysis_job(worker_id="outstanding-two")
    process_next_analysis_job(worker_id="outstanding-three")

    assert AssignmentProposal.objects.count() == 0


@pytest.mark.parametrize("terminal", ["batch", "task"])
def test_task_10_9_does_not_propose_after_work_is_terminated(terminal: str) -> None:
    batch = create_work_batch(name=f"Terminated consensus {terminal}")
    first, _assignment = submitted_revision(f"terminated-{terminal}-one", batch=batch)
    submitted_revision(f"terminated-{terminal}-two", batch=batch, task=first.task)
    submitted_revision(f"terminated-{terminal}-three", batch=batch, task=first.task)
    if terminal == "batch":
        freeze_work_batch(batch_id=batch.batch_id)
    else:
        cancel_task(task_id=first.task_id, reason="No longer required")

    process_next_analysis_job(worker_id=f"terminated-{terminal}-one")
    process_next_analysis_job(worker_id=f"terminated-{terminal}-two")
    process_next_analysis_job(worker_id=f"terminated-{terminal}-three")

    assert AssignmentProposal.objects.count() == 0


def test_pap_aae_sc_008_registered_audits_freeze_inputs_without_mutating_annotation() -> None:
    revision, assignment = submitted_revision("registered-audit")
    client_session_id = uuid4()
    active_lease_id = uuid4()
    for sequence_no, monotonic_ms in ((1, 100.0), (2, 50.0)):
        ActivityEvent.objects.create(
            worker=revision.worker,
            assignment=assignment,
            draft_cycle=revision.draft_cycle,
            client_session_id=client_session_id,
            active_lease_id=active_lease_id,
            sequence_no=sequence_no,
            event_type=ActivityEvent.EventType.INTERACTION,
            client_monotonic_ms=monotonic_ms,
            client_wall_time_ms=1_000 + sequence_no,
            visibility=ActivityEvent.Visibility.VISIBLE,
            focus=True,
            interaction_type="point_commit",
            client_build_sha="audit-test-build",
            active_time_rule_version=assignment.task.active_time_rule_version,
        )
    administrator = User.objects.create_user(
        username="registered-audit-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    admin = Client()
    admin.force_login(administrator)
    worker = Client()
    worker.force_login(revision.worker)
    path = f"/api/admin/revisions/{revision.revision_id}/audit-runs"
    original_revision = AnnotationRevision.objects.values("state", "state_sha").get(pk=revision.pk)
    original_draft = CurrentDraft.objects.values("state", "state_sha").get(
        draft_cycle=revision.draft_cycle
    )

    assert (
        worker.post(
            path,
            data=json.dumps({"audit_type": "canonical_structure"}),
            content_type="application/json",
        ).status_code
        == 403
    )
    assert (
        admin.post(
            path,
            data=json.dumps({"audit_type": "../../arbitrary-script.py"}),
            content_type="application/json",
        ).status_code
        == 400
    )
    created_runs: dict[str, str] = {}
    for audit_type in ("canonical_structure", "scope_portal", "time_integrity"):
        response = admin.post(
            path,
            data=json.dumps({"audit_type": audit_type}),
            content_type="application/json",
        )
        assert response.status_code == 202
        assert response.json()["audit_type"] == audit_type
        assert response.json()["input_sha256"]
        assert response.json()["status"] == AnalysisJob.Status.PENDING
        created_runs[audit_type] = response.json()["run_id"]

    while process_next_analysis_job(worker_id="registered-audit-worker") is not None:
        pass

    for audit_type, run_id in created_runs.items():
        response = admin.get(f"/api/admin/audit-runs/{run_id}")
        assert response.status_code == 200
        assert response.json()["artifact"] is not None, response.json()
        assert response.json()["artifact"]["audit_type"] == audit_type
        assert response.json()["artifact"]["input_sha256"] == response.json()["input_sha256"]
        assert response.json()["status"] == AnalysisJob.Status.SUCCEEDED
    time_artifact = AuditArtifact.objects.get(audit_type="time_integrity")
    assert time_artifact.requires_review is True
    assert time_artifact.findings == ["client_clock_non_monotonic"]
    assert AuditArtifact.objects.get(audit_type="canonical_structure").requires_review is False
    assert AuditArtifact.objects.get(audit_type="scope_portal").requires_review is False
    assert AnalysisJob.objects.get(job_id=created_runs["time_integrity"]).input_manifest == {
        "active_time_rule_version": assignment.task.active_time_rule_version,
        "activity_event_ids": [
            str(event_id)
            for event_id in ActivityEvent.objects.order_by("event_id").values_list(
                "event_id", flat=True
            )
        ],
        "activity_events_sha256": time_artifact.input_manifest["activity_events_sha256"],
        "audit_type": "time_integrity",
        "meta_copy_version": revision.meta_copy_version,
        "meta_schema_version": revision.meta_schema_version,
        "prediction_artifact_id": None,
        "prediction_artifact_sha256": "",
        "revision_id": str(revision.revision_id),
        "revision_state_sha": revision.state_sha,
        "task_mode": assignment.task.mode,
    }
    assert (
        AnnotationRevision.objects.values("state", "state_sha").get(pk=revision.pk)
        == original_revision
    )
    assert (
        CurrentDraft.objects.values("state", "state_sha").get(draft_cycle=revision.draft_cycle)
        == original_draft
    )
    assert TaskDeliverySelection.objects.count() == 0
    assert AuditEvent.objects.filter(
        actor=administrator,
        action="audit_run.requested",
        target_id=created_runs["time_integrity"],
    ).exists()
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditArtifact.objects.filter(pk=time_artifact.pk).update(requires_review=False)
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditArtifact.objects.filter(pk=time_artifact.pk).delete()
    with pytest.raises(DatabaseError), transaction.atomic():
        AnalysisJob.objects.filter(job_id=created_runs["time_integrity"]).update(input_manifest={})
