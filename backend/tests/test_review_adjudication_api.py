from __future__ import annotations

import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from activity.services import derive_assignment_activity
from django.db import DatabaseError, transaction
from django.test import Client
from django.utils import timezone
from identity.models import AuditEvent, DataNoticeAcceptance, User
from identity.services import CURRENT_DATA_NOTICE_VERSION
from media.models import Asset, MediaVariant
from work.jobs import eligible_task_input_manifest, process_next_analysis_job
from work.models import (
    AdjudicatedRevision,
    AnnotationRevision,
    Assignment,
    DraftCycle,
    ReviewRecord,
    ReworkRequest,
    Task,
    TaskDeliverySelection,
)
from work.services import assign_task, create_task_draft, create_work_batch, publish_task

pytestmark = pytest.mark.django_db
SUBMISSION_VERSIONS = {
    "client_build_sha": "test-client-build",
    "interaction_contract_version": "annotation-interaction-v1",
    "viewer_version": "annotation-viewer-v1",
}


def request_json(client: Client, method: str, path: str, payload: dict[str, object]):
    return getattr(client, method)(path, data=json.dumps(payload), content_type="application/json")


def worker(username: str) -> User:
    created = User.objects.create_user(
        username=username,
        password=None,
        role=User.Role.WORKER,
        must_change_password=False,
    )
    DataNoticeAcceptance.objects.create(worker=created, notice_version=CURRENT_DATA_NOTICE_VERSION)
    return created


def published_task(suffix: str) -> Task:
    asset = Asset.objects.create(source_key=f"panoramas/review-{suffix}")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key=f"cos://review-{suffix}/compressed",
        object_version=f"version-{suffix}",
        content_sha256="a" * 64,
        content_length=100,
        content_crc64ecma="123",
        width=16,
        height=8,
        format="jpeg",
        role=MediaVariant.Role.COMPRESSED,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    return publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=[variant],
            mode=Task.Mode.MANUAL,
            external_task_key=f"review-{suffix}",
        ).task_id
    )


def complete_state() -> dict[str, object]:
    pair_id = "00000000-0000-4000-8000-000000000001"
    return {
        "difficulty": ["trivial"],
        "geometry_attempt_reason_text": "",
        "geometry_attempt_status": "best_effort_complete",
        "pairs": [
            {
                "bottom": {
                    "point_id": "00000000-0000-4000-8000-000000000003",
                    "u": 0.12,
                    "v": 0.88,
                },
                "order_index": 0,
                "pair_id": pair_id,
                "top": {
                    "point_id": "00000000-0000-4000-8000-000000000002",
                    "u": 0.1,
                    "v": 0.08,
                },
            }
        ],
        "portals": [],
        "seam_anchor_pair_id": pair_id,
        "scope_reason_codes": [],
        "scope_reason_text": "",
        "worker_scope_observation": "annotatable",
    }


def submit_assignment(
    *, assigned_worker: User, task: Task, state: dict[str, object] | None = None
) -> tuple[Assignment, AnnotationRevision, Client, str]:
    assignment = assign_task(
        batch=create_work_batch(name=f"Review batch {assigned_worker.username}"),
        task=task,
        worker=assigned_worker,
    )
    tab_id = str(uuid4())
    DataNoticeAcceptance.objects.get_or_create(
        worker=assigned_worker, notice_version=CURRENT_DATA_NOTICE_VERSION
    )
    client = Client()
    client.force_login(assigned_worker)
    acquired = request_json(
        client,
        "post",
        "/api/workspace/acquire",
        {"client_instance_id": str(uuid4()), "tab_id": tab_id, "takeover": False},
    )
    assert acquired.status_code == 201
    opened = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/open",
        {"tab_id": tab_id},
    )
    assert opened.status_code == 200
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    assert client.get(draft_path, {"tab_id": tab_id}).status_code == 200
    saved = request_json(
        client,
        "put",
        draft_path,
        {
            "expected_draft_version": 0,
            "state": state or complete_state(),
            "tab_id": tab_id,
        },
    )
    assert saved.status_code == 200
    submitted = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": saved.json()["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )
    assert submitted.status_code == 201
    return assignment, AnnotationRevision.objects.get(assignment=assignment), client, tab_id


def admin_client(username: str = "review-admin") -> tuple[User, Client]:
    administrator = User.objects.create_user(
        username=username,
        password=None,
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    client = Client()
    client.force_login(administrator)
    return administrator, client


def test_pap_drr_sc_014_admin_accepts_or_requests_changes_for_a_specific_revision() -> None:
    task = published_task("outcomes")
    first_assignment, accepted_revision, _, _ = submit_assignment(
        assigned_worker=worker("accepted-worker"), task=task
    )
    second_assignment, changed_revision, _, _ = submit_assignment(
        assigned_worker=worker("changed-worker"), task=task
    )
    administrator, client = admin_client()

    accepted = request_json(
        client,
        "post",
        f"/api/admin/revisions/{accepted_revision.revision_id}/review",
        {"outcome": "accepted", "reason": ""},
    )
    missing_reason = request_json(
        client,
        "post",
        f"/api/admin/revisions/{changed_revision.revision_id}/review",
        {"outcome": "changes_requested", "reason": ""},
    )
    changes_requested = request_json(
        client,
        "post",
        f"/api/admin/revisions/{changed_revision.revision_id}/review",
        {"outcome": "changes_requested", "reason": "Please correct the doorway."},
    )

    assert accepted.status_code == changes_requested.status_code == 201
    assert missing_reason.status_code == 400
    assert missing_reason.json() == {"error": {"code": "review_reason_required"}}
    first_assignment.refresh_from_db()
    second_assignment.refresh_from_db()
    assert first_assignment.review_state == Assignment.ReviewState.ACCEPTED
    assert first_assignment.work_state == Assignment.WorkState.SUBMITTED
    assert second_assignment.review_state == Assignment.ReviewState.CHANGES_REQUESTED
    assert second_assignment.queue_state == Assignment.QueueState.NEEDS_REVISIT
    assert second_assignment.work_state == Assignment.WorkState.SUBMITTED
    assert DraftCycle.objects.filter(assignment=second_assignment).count() == 1
    audit_event = AuditEvent.objects.get(
        actor=administrator,
        target_worker=second_assignment.worker,
        action="revision.reviewed",
    )
    assert audit_event.target_type == "review_record"
    assert audit_event.target_id
    assert audit_event.reason == "Please correct the doorway."
    assert audit_event.correlation_id is not None


def test_pap_drr_sc_015_review_correction_is_append_only_and_projects_the_latest_result() -> None:
    assignment, revision, _, _ = submit_assignment(
        assigned_worker=worker("review-correction-worker"), task=published_task("correction")
    )
    _, client = admin_client("review-correction-admin")
    path = f"/api/admin/revisions/{revision.revision_id}/review"

    accepted = request_json(client, "post", path, {"outcome": "accepted", "reason": ""})
    corrected = request_json(
        client,
        "post",
        path,
        {"outcome": "changes_requested", "reason": "A later review found a missing portal."},
    )

    assert accepted.status_code == corrected.status_code == 201
    records = list(ReviewRecord.objects.filter(revision=revision).order_by("created_at"))
    assert len(records) == 2
    assert records[1].supersedes == records[0]
    assignment.refresh_from_db()
    assert assignment.review_state == Assignment.ReviewState.CHANGES_REQUESTED
    with pytest.raises(DatabaseError), transaction.atomic():
        ReviewRecord.objects.filter(pk=records[0].pk).update(reason="rewritten")
    with pytest.raises(DatabaseError), transaction.atomic():
        ReviewRecord.objects.filter(pk=records[0].pk).delete()


def test_pap_drr_sc_007_adjudication_is_admin_authored_and_selected_without_overwriting_sources() -> (
    None
):
    task = published_task("adjudication")
    first_assignment, first_revision, _, _ = submit_assignment(
        assigned_worker=worker("adjudication-worker-one"), task=task
    )
    _, second_revision, _, _ = submit_assignment(
        assigned_worker=worker("adjudication-worker-two"), task=task
    )
    administrator, client = admin_client("adjudication-admin")
    adjudicated_state = deepcopy(first_revision.state)
    adjudicated_state["pairs"][0]["top"]["u"] = 0.2

    response = request_json(
        client,
        "post",
        f"/api/admin/tasks/{task.task_id}/adjudications",
        {
            "reason": "Resolve the two source revisions.",
            "source_revision_ids": [
                str(first_revision.revision_id),
                str(second_revision.revision_id),
            ],
            "state": adjudicated_state,
        },
    )

    assert response.status_code == 201
    adjudication = AdjudicatedRevision.objects.get(task=task)
    selection = TaskDeliverySelection.objects.get(task=task)
    assert adjudication.actor == administrator
    assert adjudication.source_revision_ids == [
        str(first_revision.revision_id),
        str(second_revision.revision_id),
    ]
    assert adjudication.state == adjudicated_state
    assert selection.adjudicated_revision == adjudication
    assert selection.worker_revision_id is None
    assert AnnotationRevision.objects.filter(task=task).count() == 2
    first_assignment.refresh_from_db()
    assert first_assignment.review_state == Assignment.ReviewState.UNREVIEWED
    assert AuditEvent.objects.filter(actor=administrator, action="task.adjudicated").exists()
    with pytest.raises(DatabaseError), transaction.atomic():
        AdjudicatedRevision.objects.filter(pk=adjudication.pk).update(reason="rewritten")
    with pytest.raises(DatabaseError), transaction.atomic():
        TaskDeliverySelection.objects.filter(pk=selection.pk).delete()


def test_pap_drr_sc_008_new_worker_revision_does_not_move_an_existing_delivery_selection() -> None:
    task = published_task("selection")
    assigned_worker = worker("selection-worker")
    assignment, first_revision, worker_client, tab_id = submit_assignment(
        assigned_worker=assigned_worker, task=task
    )
    _, client = admin_client("selection-admin")
    review = request_json(
        client,
        "post",
        f"/api/admin/revisions/{first_revision.revision_id}/review",
        {"outcome": "accepted", "reason": ""},
    )
    assert review.status_code == 201
    selected = request_json(
        client,
        "post",
        f"/api/admin/tasks/{task.task_id}/delivery-selection",
        {"reason": "Use the accepted worker result.", "worker_revision_id": str(first_revision.pk)},
    )
    assert selected.status_code == 201

    revised = request_json(
        worker_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/revise",
        {"tab_id": tab_id},
    )
    assert revised.status_code == 201
    draft = worker_client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/draft", {"tab_id": tab_id}
    ).json()
    second = request_json(
        worker_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": draft["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )

    assert second.status_code == 201
    assignment.refresh_from_db()
    assert assignment.review_state == Assignment.ReviewState.UNREVIEWED
    assert TaskDeliverySelection.objects.get(task=task).worker_revision == first_revision


def test_delivery_selection_rejects_an_unreviewed_worker_revision() -> None:
    task = published_task("unreviewed-selection")
    _, revision, _, _ = submit_assignment(
        assigned_worker=worker("unreviewed-selection-worker"), task=task
    )
    _, client = admin_client("unreviewed-selection-admin")

    response = request_json(
        client,
        "post",
        f"/api/admin/tasks/{task.task_id}/delivery-selection",
        {"reason": "Not reviewed yet.", "worker_revision_id": str(revision.pk)},
    )

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "delivery_revision_not_accepted"}}
    assert not TaskDeliverySelection.objects.exists()


def test_pap_drr_sc_013_pap_drr_sc_010_pap_drr_sc_011_scope_rework_lifecycle() -> None:
    task = published_task("scope-rework")
    source_state = complete_state()
    source_state.update(
        {
            "scope_reason_codes": ["insufficient_evidence"],
            "worker_scope_observation": "representation_oos",
        }
    )
    assignment, source_revision, worker_client, tab_id = submit_assignment(
        assigned_worker=worker("scope-rework-worker"),
        task=task,
        state=source_state,
    )
    process_next_analysis_job(worker_id="initial-assessment")
    _, administrator = admin_client("scope-rework-admin")
    reviewed = request_json(
        administrator,
        "post",
        f"/api/admin/revisions/{source_revision.revision_id}/review",
        {"outcome": "changes_requested", "reason": "The final scope is annotatable."},
    )
    assert reviewed.status_code == 201
    adjudicated = request_json(
        administrator,
        "post",
        f"/api/admin/tasks/{task.task_id}/adjudications",
        {
            "reason": "Resolve scope as annotatable.",
            "source_revision_ids": [str(source_revision.revision_id)],
            "state": complete_state(),
        },
    )
    assert adjudicated.status_code == 201
    created = request_json(
        administrator,
        "post",
        f"/api/admin/revisions/{source_revision.revision_id}/rework-request",
        {
            "adjudication_id": adjudicated.json()["adjudication_id"],
            "due_at": (timezone.now() + timedelta(days=1)).isoformat(),
            "instruction": "Please submit an annotatable revision.",
        },
    )
    assert created.status_code == 201
    request_id = created.json()["request_id"]

    foreign_client = Client()
    foreign_client.force_login(worker("scope-rework-foreign"))
    assert foreign_client.get("/api/worker/rework-requests").json() == {"requests": []}
    foreign_tab_id = str(uuid4())
    assert (
        request_json(
            foreign_client,
            "post",
            "/api/workspace/acquire",
            {
                "client_instance_id": str(uuid4()),
                "tab_id": foreign_tab_id,
                "takeover": False,
            },
        ).status_code
        == 201
    )
    foreign_accept = request_json(
        foreign_client,
        "post",
        f"/api/worker/rework-requests/{request_id}/accept",
        {"tab_id": foreign_tab_id},
    )
    assert foreign_accept.status_code == 404
    assert foreign_accept.json() == {"error": {"code": "resource_not_found"}}
    blocked_revision = request_json(
        worker_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/revise",
        {"tab_id": tab_id},
    )
    assert blocked_revision.status_code == 409
    assert blocked_revision.json() == {"error": {"code": "revision_feedback_requires_rework"}}

    pending = worker_client.get("/api/worker/rework-requests").json()["requests"]
    assert pending == [
        {
            "assignment_id": str(assignment.assignment_id),
            "due_at": created.json()["due_at"],
            "instruction": "Please submit an annotatable revision.",
            "request_id": request_id,
            "status": "pending",
        }
    ]
    assert "state" not in str(pending)
    with patch("work.services.timezone.now", return_value=timezone.now() + timedelta(days=2)):
        overdue = worker_client.get("/api/worker/rework-requests").json()["requests"]
    assert overdue[0]["status"] == "rework_overdue"
    accepted = request_json(
        worker_client,
        "post",
        f"/api/worker/rework-requests/{request_id}/accept",
        {"tab_id": tab_id},
    )
    assert accepted.status_code == 201
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    draft = worker_client.get(draft_path, {"tab_id": tab_id}).json()
    assert draft["state"] == source_revision.state
    unchanged = request_json(
        worker_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": draft["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )
    assert unchanged.status_code == 409
    assert unchanged.json() == {"error": {"code": "rework_submission_incomplete"}}
    saved = request_json(
        worker_client,
        "put",
        draft_path,
        {
            "expected_draft_version": 0,
            "state": complete_state(),
            "tab_id": tab_id,
        },
    )
    activity_session_id = str(uuid4())
    active_lease_id = str(uuid4())
    for sequence_no, event_type, monotonic_ms in (
        (1, "interaction", 0),
        (2, "idle", 5_000),
    ):
        recorded = request_json(
            worker_client,
            "post",
            "/api/worker/activity-events",
            {
                "active_lease_id": active_lease_id,
                "active_time_rule_version": "active-time-v1",
                "assignment_id": str(assignment.assignment_id),
                "client_build_sha": "test-build",
                "client_monotonic_ms": monotonic_ms,
                "client_session_id": activity_session_id,
                "client_wall_time_ms": 1_700_000_000_000 + monotonic_ms,
                "draft_cycle_id": draft["draft_cycle_id"],
                "event_id": str(uuid4()),
                "event_type": event_type,
                "focus": True,
                "interaction_type": "annotation_2d_edit" if event_type == "interaction" else None,
                "sequence_no": sequence_no,
                "tab_id": tab_id,
                "visibility": "visible",
            },
        )
        assert recorded.status_code == 201
    submitted = request_json(
        worker_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": saved.json()["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )
    assert submitted.status_code == 201
    feedback_revision = AnnotationRevision.objects.get(revision_id=submitted.json()["revision_id"])
    rework = ReworkRequest.objects.get(request_id=request_id)
    assert rework.initial_submission_revision == source_revision
    assert rework.completed_revision == feedback_revision
    assert rework.exposed_at is not None
    assert AnnotationRevision.objects.filter(pk=source_revision.pk).exists()
    activity = derive_assignment_activity(
        actor=assignment.worker,
        assignment_id=assignment.assignment_id,
    )
    assert activity["rework_ms"] == 5_000
    assert activity["revision_ms"] == 0

    process_next_analysis_job(worker_id="feedback-assessment")
    manifest = eligible_task_input_manifest(
        task_id=task.task_id,
        batch_id=assignment.batch_id,
    )
    assert [item["revision_id"] for item in manifest["inputs"]] == [
        str(source_revision.revision_id)
    ]


@pytest.mark.parametrize("existing_delivery", [False, True])
def test_pap_drr_sc_016_scope_only_rework_action_is_atomic_and_geometry_opaque(
    existing_delivery: bool,
) -> None:
    suffix = "with-delivery" if existing_delivery else "without-delivery"
    task = published_task(f"scope-rework-action-{suffix}")
    source_state = complete_state()
    pair_id = "00000000-0000-4000-8000-000000000001"
    source_state.update(
        {
            "geometry_attempt_status": "partial",
            "portals": [
                {
                    "evidence_status": "direct_visible",
                    "geometry": {
                        "bottom_left": {"u": 0.2, "v": 0.8},
                        "bottom_right": {"u": 0.3, "v": 0.8},
                        "top_left": {"u": 0.2, "v": 0.2},
                        "top_right": {"u": 0.3, "v": 0.2},
                    },
                    "host_edge_ref": pair_id,
                    "kind": "door",
                    "portal_id": "00000000-0000-4000-8000-000000000004",
                }
            ],
            "scope_reason_codes": ["insufficient_evidence"],
            "worker_scope_observation": "representation_oos",
        }
    )
    assignment, source_revision, worker_client, _ = submit_assignment(
        assigned_worker=worker(f"scope-rework-action-worker-{suffix}"),
        task=task,
        state=source_state,
    )
    administrator, client = admin_client(f"scope-rework-action-admin-{suffix}")
    endpoint = f"/api/admin/revisions/{source_revision.revision_id}/scope-rework"
    due_at = (timezone.now() + timedelta(days=2)).isoformat()

    worker_attempt = request_json(
        worker_client,
        "post",
        endpoint,
        {
            "due_at": due_at,
            "instruction": "Rework only from your own observations.",
            "reason": "The final scope is annotatable.",
        },
    )
    assert worker_attempt.status_code == 403
    assert worker_attempt.json() == {"error": {"code": "admin_required"}}

    geometry_injection = request_json(
        client,
        "post",
        endpoint,
        {
            "due_at": due_at,
            "instruction": "Rework only from your own observations.",
            "reason": "The final scope is annotatable.",
            "state": complete_state(),
        },
    )
    assert geometry_injection.status_code == 400
    assert geometry_injection.json() == {"error": {"code": "invalid_scope_rework"}}

    invalid_due = request_json(
        client,
        "post",
        endpoint,
        {
            "due_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
            "instruction": "Rework only from your own observations.",
            "reason": "The final scope is annotatable.",
        },
    )
    assert invalid_due.status_code == 400
    assert invalid_due.json() == {"error": {"code": "rework_due_invalid"}}
    assert not ReviewRecord.objects.filter(revision=source_revision).exists()
    assert not AdjudicatedRevision.objects.filter(task=task).exists()
    assert not TaskDeliverySelection.objects.filter(task=task).exists()
    assert not ReworkRequest.objects.filter(initial_submission_revision=source_revision).exists()

    existing_selection: TaskDeliverySelection | None = None
    if existing_delivery:
        _, delivery_revision, _, _ = submit_assignment(
            assigned_worker=worker("scope-rework-selected-worker"),
            task=task,
        )
        accepted = request_json(
            client,
            "post",
            f"/api/admin/revisions/{delivery_revision.revision_id}/review",
            {"outcome": "accepted", "reason": ""},
        )
        assert accepted.status_code == 201
        selected = request_json(
            client,
            "post",
            f"/api/admin/tasks/{task.task_id}/delivery-selection",
            {
                "reason": "Keep the accepted independent delivery.",
                "worker_revision_id": str(delivery_revision.revision_id),
            },
        )
        assert selected.status_code == 201
        existing_selection = TaskDeliverySelection.objects.get(
            selection_id=selected.json()["selection_id"]
        )

    created = request_json(
        client,
        "post",
        endpoint,
        {
            "due_at": due_at,
            "instruction": "Rework only from your own observations.",
            "reason": "The final scope is annotatable.",
        },
    )

    assert created.status_code == 201
    assert set(created.json()) == {
        "adjudication_id",
        "assignment_id",
        "due_at",
        "instruction",
        "request_id",
        "review_id",
        "status",
    }
    review = ReviewRecord.objects.get(review_id=created.json()["review_id"])
    adjudication = AdjudicatedRevision.objects.get(
        adjudication_id=created.json()["adjudication_id"]
    )
    rework = ReworkRequest.objects.get(request_id=created.json()["request_id"])
    expected_state = deepcopy(source_revision.state)
    expected_state.update(
        {
            "scope_reason_codes": [],
            "scope_reason_text": "",
            "worker_scope_observation": "annotatable",
        }
    )
    assert review.outcome == ReviewRecord.Outcome.CHANGES_REQUESTED
    assert review.reason == "The final scope is annotatable."
    assert adjudication.state == expected_state
    assert adjudication.state["pairs"] == source_revision.state["pairs"]
    assert adjudication.state["portals"] == source_revision.state["portals"]
    assert adjudication.source_revision_ids == [str(source_revision.revision_id)]
    assert rework.initial_submission_revision == source_revision
    assert rework.source_adjudication == adjudication
    selections = list(TaskDeliverySelection.objects.filter(task=task))
    assert selections == ([] if existing_selection is None else [existing_selection])
    if existing_selection is not None:
        assert not TaskDeliverySelection.objects.filter(supersedes=existing_selection).exists()
    source_revision.refresh_from_db()
    assert source_revision.state == source_state
    assignment.refresh_from_db()
    assert assignment.review_state == Assignment.ReviewState.CHANGES_REQUESTED
    assert AuditEvent.objects.filter(
        actor=administrator,
        action="rework.created",
        target_id=str(rework.request_id),
    ).exists()
