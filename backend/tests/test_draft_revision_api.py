from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from activity.models import ActivityEvent
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.test import Client
from identity.models import AuditEvent, DataNoticeAcceptance, User
from identity.services import CURRENT_DATA_NOTICE_VERSION
from media.models import Asset, MediaVariant
from work.annotation_state import annotation_state_sha, canonicalize_annotation_state
from work.models import (
    AnnotationRevision,
    Assignment,
    BlockDisposition,
    BlockReport,
    CurrentDraft,
    DraftCycle,
    OperationalIssue,
    Task,
    WorkBatch,
)
from work.services import (
    assign_task,
    cancel_task,
    create_task_draft,
    create_work_batch,
    publish_task,
)

pytestmark = pytest.mark.django_db
SUBMISSION_VERSIONS = {
    "client_build_sha": "test-client-build",
    "interaction_contract_version": "annotation-interaction-v1",
    "viewer_version": "annotation-viewer-v1",
}


def worker(username: str) -> User:
    return User.objects.create_user(
        username=username,
        password=None,
        role=User.Role.WORKER,
        must_change_password=False,
    )


def published_assignment(username: str = "draft-worker") -> tuple[User, Assignment]:
    assigned_worker = worker(username)
    asset = Asset.objects.create(source_key=f"panoramas/{username}")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key=f"cos://{username}/compressed",
        object_version=f"version-{username}",
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
    task = create_task_draft(
        asset=asset,
        media_variants=[variant],
        mode=Task.Mode.MANUAL,
        external_task_key=f"external-{username}",
    )
    task = publish_task(task_id=task.task_id)
    batch = create_work_batch(name=f"Batch {username}")
    return assigned_worker, assign_task(batch=batch, task=task, worker=assigned_worker)


def client_with_workspace(user: User, tab_id: str, *, takeover: bool = False) -> Client:
    DataNoticeAcceptance.objects.get_or_create(
        worker=user, notice_version=CURRENT_DATA_NOTICE_VERSION
    )
    client = Client()
    client.force_login(user)
    response = client.post(
        "/api/workspace/acquire",
        data=json.dumps(
            {
                "client_instance_id": str(uuid4()),
                "tab_id": tab_id,
                "takeover": takeover,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code in {200, 201}, response.json()
    return client


def request_json(client: Client, method: str, path: str, payload: dict[str, object]):
    return getattr(client, method)(path, data=json.dumps(payload), content_type="application/json")


def open_assignment(client: Client, assignment: Assignment, tab_id: str) -> None:
    response = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/open",
        {"tab_id": tab_id},
    )
    assert response.status_code == 200, response.json()


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


def canonical_for(assignment: Assignment, state: object) -> dict[str, object]:
    return canonicalize_annotation_state(
        state,
        meta_schema_version=assignment.task.meta_schema_version or "",
        task_mode=assignment.task.mode or "",
    )


def state_sha_for(assignment: Assignment, state: object) -> str:
    return annotation_state_sha(
        state,
        meta_schema_version=assignment.task.meta_schema_version or "",
        task_mode=assignment.task.mode or "",
    )


def test_pap_drr_sc_001_pap_prv_sc_012_current_draft_autosaves_and_rejects_invalid_state() -> None:
    assigned_worker, assignment = published_assignment()
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    path = f"/api/worker/assignments/{assignment.assignment_id}/draft"

    initial = client.get(path, {"tab_id": tab_id})

    assert initial.status_code == 200
    assert initial.json()["draft_version"] == 0
    assert initial.json()["state"] == {
        "difficulty": [],
        "geometry_attempt_reason_text": "",
        "geometry_attempt_status": None,
        "pairs": [],
        "portals": [],
        "seam_anchor_pair_id": None,
        "scope_reason_codes": [],
        "scope_reason_text": "",
        "worker_scope_observation": None,
    }

    state = complete_state()
    saved = request_json(
        client,
        "put",
        path,
        {"expected_draft_version": 0, "state": state, "tab_id": tab_id},
    )
    stale_state = deepcopy(state)
    stale_state["pairs"][0]["top"]["u"] = 0.2
    stale = request_json(
        client,
        "put",
        path,
        {"expected_draft_version": 0, "state": stale_state, "tab_id": tab_id},
    )
    restored = client.get(path, {"tab_id": tab_id})

    assert saved.status_code == 200
    assert saved.json()["draft_version"] == 1
    assert saved.json()["updated_at"].endswith("Z")
    assert saved.json()["state"] == canonical_for(assignment, state)
    assert saved.json()["state_sha"] == state_sha_for(assignment, state)
    assert stale.status_code == 409
    assert stale.json() == {
        "error": {
            "code": "draft_conflict",
            "server_draft_version": 1,
            "server_updated_at": saved.json()["updated_at"],
        }
    }

    invalid_state = deepcopy(state)
    invalid_state["pairs"][0]["top"]["u"] = 2
    invalid = request_json(
        client,
        "put",
        path,
        {"expected_draft_version": 1, "state": invalid_state, "tab_id": tab_id},
    )
    assert invalid.status_code == 400
    assert invalid.json() == {
        "error": {
            "code": "annotation_coordinate_out_of_range",
            "field": "top.u",
            "pair_id": state["pairs"][0]["pair_id"],
            "pair_index": 0,
            "point_id": state["pairs"][0]["top"]["point_id"],
            "severity": "error",
        }
    }
    assert list(
        OperationalIssue.objects.values_list("kind", "error_code").order_by("created_at")
    ) == [
        (OperationalIssue.Kind.DRAFT_SAVE, "draft_conflict"),
        (OperationalIssue.Kind.ANNOTATION_STRUCTURE, "annotation_coordinate_out_of_range"),
    ]
    assert restored.json() == saved.json()
    assert DraftCycle.objects.filter(assignment=assignment, closed_at=None).count() == 1
    assert CurrentDraft.objects.count() == 1


def test_pap_ann_sc_010_and_pas_sc_001_manual_contract_rejects_model_issue() -> None:
    assigned_worker, assignment = published_assignment("manual-meta-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)

    assignment_response = client.get(f"/api/worker/assignments/{assignment.assignment_id}")
    contract = assignment_response.json()["task"]["meta_contract"]
    assert assignment_response.json()["task"]["active_time_rule_version"] == "active-time-v1"
    assert contract["schema_version"] == "annotation-meta-v1"
    assert contract["copy_version"] == "annotation-meta-copy-v1"
    assert "model_issue_options" not in contract
    serialized = json.dumps(assignment_response.json())
    assert all(
        forbidden not in serialized
        for forbidden in ("prediction", "model_issue", "model_risk", "assist")
    )

    state = {**complete_state(), "model_issue": []}
    rejected = request_json(
        client,
        "put",
        f"/api/worker/assignments/{assignment.assignment_id}/draft",
        {"expected_draft_version": 0, "state": state, "tab_id": tab_id},
    )

    assert rejected.status_code == 400
    assert rejected.json() == {
        "error": {
            "code": "annotation_model_issue_forbidden",
            "field": "model_issue",
            "severity": "error",
        }
    }
    assert not CurrentDraft.objects.filter(draft_cycle__assignment=assignment).exists()


def test_draft_foreign_and_missing_assignments_are_indistinguishable() -> None:
    owner, assignment = published_assignment("draft-owner")
    stranger = worker("draft-stranger")
    owner_tab = str(uuid4())
    owner_client = client_with_workspace(owner, owner_tab)
    open_assignment(owner_client, assignment, owner_tab)
    stranger_tab = str(uuid4())
    stranger_client = client_with_workspace(stranger, stranger_tab)

    foreign = stranger_client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/draft", {"tab_id": stranger_tab}
    )
    missing = stranger_client.get(
        f"/api/worker/assignments/{uuid4()}/draft", {"tab_id": stranger_tab}
    )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"error": {"code": "resource_not_found"}}
    assert CurrentDraft.objects.count() == 0


def test_draft_save_rejects_taken_over_workspace_and_terminated_task() -> None:
    assigned_worker, assignment = published_assignment("draft-lease")
    old_tab = str(uuid4())
    old_client = client_with_workspace(assigned_worker, old_tab)
    open_assignment(old_client, assignment, old_tab)
    path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    initial = old_client.get(path, {"tab_id": old_tab}).json()

    new_tab = str(uuid4())
    client_with_workspace(assigned_worker, new_tab, takeover=True)
    lease_lost = request_json(
        old_client,
        "put",
        path,
        {"expected_draft_version": 0, "state": complete_state(), "tab_id": old_tab},
    )
    submit_after_takeover = request_json(
        old_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": initial["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": old_tab,
        },
    )

    assert lease_lost.status_code == 409
    assert lease_lost.json() == {"error": {"code": "workspace_lease_lost"}}
    assert submit_after_takeover.status_code == 409
    assert submit_after_takeover.json() == {"error": {"code": "workspace_lease_lost"}}
    assert AnnotationRevision.objects.count() == 0
    assert CurrentDraft.objects.get().state_sha == initial["state_sha"]

    current_tab = str(uuid4())
    current_client = client_with_workspace(assigned_worker, current_tab, takeover=True)
    cancel_task(task_id=assignment.task_id, reason="Source withdrawn")
    terminated = request_json(
        current_client,
        "put",
        path,
        {"expected_draft_version": 0, "state": complete_state(), "tab_id": current_tab},
    )
    assert terminated.status_code == 409
    assert terminated.json() == {"error": {"code": "assignment_task_unavailable"}}


def test_pap_drr_sc_003_pap_pbd_sc_003_submit_freezes_draft_and_admin_reads_audited_revision() -> (
    None
):
    assigned_worker, assignment = published_assignment("submit-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    initial_draft_response = client.get(draft_path, {"tab_id": tab_id})
    assert initial_draft_response.status_code == 200
    assert all(
        forbidden not in initial_draft_response.content.decode()
        for forbidden in ("prediction", "model_issue", "model_risk", "assist")
    )
    state = complete_state()
    saved = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 0, "state": state, "tab_id": tab_id},
    ).json()
    idempotency_key = str(uuid4())
    submit_payload = {
        "client_build_sha": "client-build-abc123",
        "expected_state_sha": saved["state_sha"],
        "idempotency_key": idempotency_key,
        "interaction_contract_version": "annotation-interaction-v1",
        "locale": "en",
        "tab_id": tab_id,
        "viewer_version": "annotation-viewer-v1",
    }
    submit_path = f"/api/worker/assignments/{assignment.assignment_id}/submit"

    stale = request_json(
        client,
        "post",
        submit_path,
        {**submit_payload, "expected_state_sha": "0" * 64, "idempotency_key": str(uuid4())},
    )
    submitted = request_json(client, "post", submit_path, submit_payload)
    repeated = request_json(client, "post", submit_path, submit_payload)
    conflicting = request_json(
        client,
        "post",
        submit_path,
        {**submit_payload, "expected_state_sha": "0" * 64},
    )
    locale_conflicting = request_json(
        client,
        "post",
        submit_path,
        {**submit_payload, "locale": "zh-CN"},
    )
    version_conflicting = request_json(
        client,
        "post",
        submit_path,
        {**submit_payload, "client_build_sha": "client-build-def456"},
    )
    unsupported_viewer = request_json(
        client,
        "post",
        submit_path,
        {**submit_payload, "viewer_version": "annotation-viewer-v2"},
    )
    unsupported_locale = request_json(
        client,
        "post",
        submit_path,
        {**submit_payload, "idempotency_key": str(uuid4()), "locale": "fr"},
    )
    cancel_task(task_id=assignment.task_id, reason="Source withdrawn after submission")
    replayed_after_cancellation = request_json(client, "post", submit_path, submit_payload)

    assert stale.status_code == 409
    assert stale.json() == {"error": {"code": "draft_conflict"}}
    assert submitted.status_code == 201
    assert repeated.status_code == 200
    assert repeated.json() == submitted.json()
    assert conflicting.status_code == 409
    assert conflicting.json() == {"error": {"code": "submission_idempotency_conflict"}}
    assert locale_conflicting.status_code == 409
    assert locale_conflicting.json() == {"error": {"code": "submission_idempotency_conflict"}}
    assert version_conflicting.status_code == 409
    assert version_conflicting.json() == {"error": {"code": "submission_idempotency_conflict"}}
    assert unsupported_viewer.status_code == 400
    assert unsupported_viewer.json() == {"error": {"code": "invalid_submission"}}
    assert unsupported_locale.status_code == 400
    assert unsupported_locale.json() == {"error": {"code": "invalid_submission"}}
    assert replayed_after_cancellation.status_code == 200
    assert replayed_after_cancellation.json() == submitted.json()
    assert AnnotationRevision.objects.count() == 1
    revision = AnnotationRevision.objects.get()
    assert submitted.json() == {
        "client_build_sha": "client-build-abc123",
        "interaction_contract_version": "annotation-interaction-v1",
        "platform_release_id": "panorama-platform-v1",
        "revision_id": str(revision.revision_id),
        "revision_no": 1,
        "state_sha": saved["state_sha"],
        "submitted_at": revision.submitted_at.isoformat().replace("+00:00", "Z"),
        "verification_status": "verification_pending",
        "viewer_version": "annotation-viewer-v1",
    }
    assert revision.state == canonical_for(assignment, state)
    assert revision.source_draft_version == 1
    assert revision.meta_schema_version == assignment.task.meta_schema_version
    assert revision.meta_copy_version == assignment.task.meta_copy_version
    assert revision.submission_locale == "en"
    assert revision.platform_release_id == "panorama-platform-v1"
    assert revision.client_build_sha == "client-build-abc123"
    assert revision.viewer_version == "annotation-viewer-v1"
    assert revision.interaction_contract_version == "annotation-interaction-v1"
    assert revision.submitted_at.utcoffset() is not None
    assert revision.submitted_at.utcoffset().total_seconds() == 0
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.SUBMITTED
    assert DraftCycle.objects.get().closed_at is not None

    admin = User.objects.create_user(
        username="revision-admin",
        password=None,
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    admin_client = Client()
    admin_client.force_login(admin)
    response = admin_client.get(f"/api/admin/revisions/{revision.revision_id}")

    assert response.status_code == 200
    assert response.json()["state"] == revision.state
    assert response.json()["state_sha"] == revision.state_sha
    assert response.json()["platform_release_id"] == "panorama-platform-v1"
    assert response.json()["client_build_sha"] == "client-build-abc123"
    assert response.json()["viewer_version"] == "annotation-viewer-v1"
    assert response.json()["interaction_contract_version"] == "annotation-interaction-v1"
    assert "geometry_engine_version" not in response.json()
    # PAP-PAS-SC-001: a Manual Revision read never grows Semi initialization fields.
    assert all(
        forbidden not in response.content.decode()
        for forbidden in ("prediction", "model_issue", "model_risk", "assist")
    )
    assert AuditEvent.objects.filter(
        actor=admin,
        target_worker=assigned_worker,
        action="resource.sensitive_read",
        details__resource_kind="revision",
    ).exists()

    revision.state_sha = "f" * 64
    with pytest.raises(ValidationError):
        revision.save()
    with pytest.raises(ValidationError):
        revision.delete()

    with pytest.raises(DatabaseError), transaction.atomic():
        AnnotationRevision.objects.filter(pk=revision.pk).update(state_sha="e" * 64)
    with pytest.raises(DatabaseError), transaction.atomic():
        AnnotationRevision.objects.filter(pk=revision.pk).delete()


def test_submit_rolls_back_revision_when_closing_the_draft_cycle_fails(monkeypatch) -> None:
    assigned_worker, assignment = published_assignment("submit-rollback-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    client.get(draft_path, {"tab_id": tab_id})
    saved = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 0, "state": complete_state(), "tab_id": tab_id},
    ).json()
    original_save = DraftCycle.save

    def fail_when_closing(cycle, *args, **kwargs):
        if cycle.closed_at is not None:
            raise RuntimeError("cycle close failed")
        return original_save(cycle, *args, **kwargs)

    monkeypatch.setattr(DraftCycle, "save", fail_when_closing)
    with pytest.raises(RuntimeError, match="cycle close failed"):
        request_json(
            client,
            "post",
            f"/api/worker/assignments/{assignment.assignment_id}/submit",
            {
                **SUBMISSION_VERSIONS,
                "expected_state_sha": saved["state_sha"],
                "idempotency_key": str(uuid4()),
                "locale": "zh-CN",
                "tab_id": tab_id,
            },
        )

    assignment.refresh_from_db()
    assert AnnotationRevision.objects.count() == 0
    assert DraftCycle.objects.get(assignment=assignment).closed_at is None
    assert assignment.work_state == Assignment.WorkState.IN_PROGRESS


def test_pap_drr_sc_005_worker_revises_the_latest_revision_in_a_new_draft_cycle() -> None:
    assigned_worker, assignment = published_assignment("revision-cycle-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    client.get(draft_path, {"tab_id": tab_id})
    initial_state = complete_state()
    saved = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 0, "state": initial_state, "tab_id": tab_id},
    ).json()
    first = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": saved["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )

    revised = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/revise",
        {"tab_id": tab_id},
    )

    assert first.status_code == 201
    assert revised.status_code == 201
    assert revised.json()["work_state"] == Assignment.WorkState.IN_PROGRESS
    copied = client.get(draft_path, {"tab_id": tab_id})
    assert copied.status_code == 200
    assert copied.json()["draft_version"] == 0
    assert copied.json()["state"] == initial_state
    assert copied.json()["state_sha"] == first.json()["state_sha"]
    assert DraftCycle.objects.filter(assignment=assignment).count() == 2

    duplicate = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/revise",
        {"tab_id": tab_id},
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {"error": {"code": "assignment_not_submitted"}}
    assert DraftCycle.objects.filter(assignment=assignment).count() == 2

    changed_state = deepcopy(initial_state)
    changed_state["pairs"][0]["top"]["u"] = 0.2
    second_saved = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 0, "state": changed_state, "tab_id": tab_id},
    ).json()
    second = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": second_saved["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )

    assert second.status_code == 201
    assert second.json()["revision_no"] == 2
    revisions = list(AnnotationRevision.objects.filter(assignment=assignment))
    assert revisions[0].state == initial_state
    assert revisions[1].state == canonical_for(assignment, changed_state)
    assignment.refresh_from_db()
    assert assignment.review_state == Assignment.ReviewState.UNREVIEWED

    cancel_task(task_id=assignment.task_id, reason="Source withdrawn after revision 2")
    terminated = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/revise",
        {"tab_id": tab_id},
    )
    assert terminated.status_code == 409
    assert terminated.json() == {"error": {"code": "assignment_task_unavailable"}}
    assert DraftCycle.objects.filter(assignment=assignment).count() == 2


def test_revision_cycle_foreign_and_missing_assignments_are_indistinguishable() -> None:
    _, assignment = published_assignment("revision-cycle-owner")
    stranger = worker("revision-cycle-stranger")
    tab_id = str(uuid4())
    client = client_with_workspace(stranger, tab_id)

    foreign = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/revise",
        {"tab_id": tab_id},
    )
    missing = request_json(
        client,
        "post",
        f"/api/worker/assignments/{uuid4()}/revise",
        {"tab_id": tab_id},
    )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"error": {"code": "resource_not_found"}}
    assert DraftCycle.objects.count() == 0


def test_submit_rejects_an_unanswered_draft_without_creating_a_revision() -> None:
    assigned_worker, assignment = published_assignment("incomplete-submit-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    draft = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/draft", {"tab_id": tab_id}
    ).json()

    response = request_json(
        client,
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

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "annotation_submission_incomplete",
            "field": "state",
            "severity": "error",
        }
    }
    assert AnnotationRevision.objects.count() == 0
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.IN_PROGRESS


def test_pap_tba_sc_018_representation_oos_is_submitted_as_revision_evidence() -> None:
    assigned_worker, assignment = published_assignment("scope-evidence-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    initial = client.get(draft_path, {"tab_id": tab_id}).json()
    state = complete_state()
    state["worker_scope_observation"] = "representation_oos"
    state["scope_reason_codes"] = ["non_manhattan"]

    saved = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 0, "state": state, "tab_id": tab_id},
    )
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

    assert initial["draft_version"] == 0
    assert saved.status_code == 200
    assert submitted.status_code == 201
    revision = AnnotationRevision.objects.get(assignment=assignment)
    assert revision.state["worker_scope_observation"] == "representation_oos"
    assert revision.state["scope_reason_codes"] == ["non_manhattan"]


def test_pap_tba_sc_013_block_report_preserves_draft_and_activity_without_revision() -> None:
    assigned_worker, assignment = published_assignment("block-technical-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    draft_response = client.get(draft_path, {"tab_id": tab_id})
    draft = CurrentDraft.objects.get(draft_cycle_id=draft_response.json()["draft_cycle_id"])
    event = ActivityEvent.objects.create(
        worker=assigned_worker,
        assignment=assignment,
        draft_cycle=draft.draft_cycle,
        client_session_id=uuid4(),
        active_lease_id=uuid4(),
        sequence_no=1,
        event_type=ActivityEvent.EventType.INTERACTION,
        client_monotonic_ms=1_000,
        client_wall_time_ms=1_700_000_001_000,
        visibility=ActivityEvent.Visibility.VISIBLE,
        focus=True,
        interaction_type="annotation_2d_edit",
        client_build_sha="test-build",
        active_time_rule_version="active-time-v1",
    )

    blocked = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/block",
        {
            "reason_code": "technical_failure",
            "reason_text": "COS image could not be decoded.",
            "tab_id": tab_id,
        },
    )

    assert blocked.status_code == 201
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.BLOCKED
    assert assignment.queue_state == Assignment.QueueState.READY
    assert assignment.review_state == Assignment.ReviewState.UNREVIEWED
    assert set(BlockReport.ReasonCode.values) == {
        "conflict_of_interest",
        "image_unavailable",
        "other",
        "technical_failure",
        "temporary_worker_issue",
        "unable_to_complete",
    }
    report = assignment.block_reports.get()
    assert report.reason_schema_version == "assignment-block-v1"
    assert report.reason_code == "technical_failure"
    assert report.reason_text == "COS image could not be decoded."
    assert report.draft_cycle_id == draft.draft_cycle_id
    assert CurrentDraft.objects.filter(pk=draft.pk, draft_version=0).exists()
    assert ActivityEvent.objects.filter(pk=event.pk).exists()
    assert not AnnotationRevision.objects.filter(assignment=assignment).exists()
    assert client.get("/api/worker/batches").json()["batches"][0]["worker_complete"] is True
    queue_write = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/queue-state",
        {"queue_state": "ready", "tab_id": tab_id},
    )
    assert queue_write.status_code == 409
    assert queue_write.json() == {"error": {"code": "assignment_not_editable"}}
    assignment.refresh_from_db()
    assert assignment.queue_state == Assignment.QueueState.READY
    report.reason_code = BlockReport.ReasonCode.OTHER
    with pytest.raises(ValidationError):
        report.save()
    with pytest.raises(ValidationError):
        report.delete()


def test_block_rejects_unknown_reason_and_required_explanation_without_changing_state() -> None:
    assigned_worker, assignment = published_assignment("block-validation-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    path = f"/api/worker/assignments/{assignment.assignment_id}/block"

    unknown = request_json(
        client,
        "post",
        path,
        {"reason_code": "representation_oos", "reason_text": "", "tab_id": tab_id},
    )
    missing_text = request_json(
        client,
        "post",
        path,
        {"reason_code": "technical_failure", "reason_text": "   ", "tab_id": tab_id},
    )

    assert unknown.status_code == 400
    assert unknown.json() == {"error": {"code": "block_reason_invalid"}}
    assert missing_text.status_code == 400
    assert missing_text.json() == {"error": {"code": "block_reason_text_required"}}
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.ASSIGNED


def test_block_foreign_missing_and_taken_over_workspace_do_not_create_records() -> None:
    owner, assignment = published_assignment("block-owner")
    stranger = worker("block-stranger")
    stranger_tab = str(uuid4())
    stranger_client = client_with_workspace(stranger, stranger_tab)
    payload = {
        "reason_code": "unable_to_complete",
        "reason_text": "",
        "tab_id": stranger_tab,
    }

    foreign = request_json(
        stranger_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/block",
        payload,
    )
    missing = request_json(
        stranger_client,
        "post",
        f"/api/worker/assignments/{uuid4()}/block",
        payload,
    )

    old_tab = str(uuid4())
    old_client = client_with_workspace(owner, old_tab)
    client_with_workspace(owner, str(uuid4()), takeover=True)
    lost = request_json(
        old_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/block",
        {**payload, "tab_id": old_tab},
    )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"error": {"code": "resource_not_found"}}
    assert lost.status_code == 409
    assert lost.json() == {"error": {"code": "workspace_lease_lost"}}
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.ASSIGNED


@pytest.mark.parametrize("action", ["reopen", "reassign", "terminate"])
def test_pap_tba_sc_020_admin_explicitly_disposes_blocked_assignment(action: str) -> None:
    assigned_worker, assignment = published_assignment(f"block-disposition-{action}")
    tab_id = str(uuid4())
    worker_client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(worker_client, assignment, tab_id)
    draft = worker_client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/draft",
        {"tab_id": tab_id},
    ).json()
    blocked = request_json(
        worker_client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/block",
        {
            "reason_code": "unable_to_complete",
            "reason_text": "Cannot complete this assignment.",
            "tab_id": tab_id,
        },
    )
    assert blocked.status_code == 201

    administrator = User.objects.create_user(
        username=f"block-admin-{action}",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    admin_client = Client()
    admin_client.force_login(administrator)
    payload: dict[str, object] = {"action": action, "reason": f"Resolve by {action}."}
    replacement_worker = None
    if action == "reassign":
        replacement_worker = worker("block-replacement-worker")
        payload["worker_id"] = str(replacement_worker.worker_id)
    disposed = request_json(
        admin_client,
        "post",
        f"/api/admin/assignments/{assignment.assignment_id}/block-disposition",
        payload,
    )

    assert disposed.status_code == 201
    assignment.refresh_from_db()
    disposition = BlockDisposition.objects.get(block_report__assignment=assignment)
    assert disposition.action == action
    assert disposition.reason == f"Resolve by {action}."
    assert AuditEvent.objects.filter(action="assignment.block_disposed").exists()
    assert CurrentDraft.objects.filter(draft_id=draft["draft_id"]).exists()
    assert not AnnotationRevision.objects.filter(assignment=assignment).exists()
    if action == "reopen":
        assert assignment.work_state == Assignment.WorkState.IN_PROGRESS
        assert assignment.queue_state == Assignment.QueueState.READY
        assert disposition.replacement_assignment_id is None
    elif action == "reassign":
        assert assignment.work_state == Assignment.WorkState.BLOCKED
        replacement = Assignment.objects.get(pk=disposed.json()["replacement_assignment_id"])
        assert replacement.worker == replacement_worker
        assert replacement.task == assignment.task
        assert replacement.batch == assignment.batch
        assert disposition.replacement_assignment == replacement
    else:
        assert assignment.work_state == Assignment.WorkState.BLOCKED
        assert disposition.replacement_assignment_id is None


def test_session_revocation_blocks_draft_reads_that_can_create_server_state() -> None:
    assigned_worker, assignment = published_assignment("revoked-draft-worker")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    Session.objects.filter(session_key=client.session.session_key).delete()

    response = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/draft", {"tab_id": tab_id}
    )

    assert response.status_code == 401
    assert CurrentDraft.objects.count() == 0


def test_pap_tba_sc_014_batch_freeze_preserves_work_and_blocks_writes_until_reopen() -> None:
    assigned_worker, assignment = published_assignment("batch-freeze-worker")
    never_submitted_task = publish_task(
        task_id=create_task_draft(
            asset=assignment.task.asset,
            media_variants=assignment.task.allowed_media_variants.all(),
            mode=Task.Mode.MANUAL,
            external_task_key="batch-freeze-never-submitted",
        ).task_id
    )
    never_submitted = assign_task(
        batch=assignment.batch,
        task=never_submitted_task,
        worker=assigned_worker,
    )
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    open_assignment(client, assignment, tab_id)
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    client.get(draft_path, {"tab_id": tab_id})
    first_saved = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 0, "state": complete_state(), "tab_id": tab_id},
    ).json()
    submitted = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": first_saved["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )
    assert submitted.status_code == 201
    revised = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/revise",
        {"tab_id": tab_id},
    )
    assert revised.status_code == 201
    changed_state = deepcopy(complete_state())
    changed_state["pairs"][0]["top"]["u"] = 0.2
    second_saved = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 0, "state": changed_state, "tab_id": tab_id},
    ).json()

    admin = User.objects.create_user(
        username="batch-freeze-admin",
        password=None,
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    admin_client = Client()
    admin_client.force_login(admin)
    lifecycle_path = f"/api/admin/work-batches/{assignment.batch_id}"
    frozen = admin_client.post(f"{lifecycle_path}/freeze")
    frozen_again = admin_client.post(f"{lifecycle_path}/freeze")

    assert frozen.status_code == frozen_again.status_code == 200
    assert frozen.json()["status"] == WorkBatch.Status.FROZEN
    blocked_draft = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 1, "state": complete_state(), "tab_id": tab_id},
    )
    blocked_submit = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": second_saved["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )
    blocked_open = request_json(
        client,
        "post",
        f"/api/worker/assignments/{never_submitted.assignment_id}/open",
        {"tab_id": tab_id},
    )
    blocked_queue = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/queue-state",
        {"queue_state": Assignment.QueueState.DEFERRED, "tab_id": tab_id},
    )
    blocked_activity = request_json(
        client,
        "post",
        "/api/worker/activity-events",
        {
            "active_lease_id": str(uuid4()),
            "active_time_rule_version": "active-time-v1",
            "assignment_id": str(assignment.assignment_id),
            "client_build_sha": "batch-freeze-test",
            "client_monotonic_ms": 0,
            "client_session_id": str(uuid4()),
            "client_wall_time_ms": 1_700_000_000_000,
            "draft_cycle_id": second_saved["draft_cycle_id"],
            "event_id": str(uuid4()),
            "event_type": "interaction",
            "focus": True,
            "interaction_type": "annotation_2d_edit",
            "sequence_no": 1,
            "tab_id": tab_id,
            "visibility": "visible",
        },
    )

    for response in (
        blocked_draft,
        blocked_submit,
        blocked_open,
        blocked_queue,
        blocked_activity,
    ):
        assert response.status_code == 409
        assert response.json() == {"error": {"code": "batch_not_open"}}
    assert AnnotationRevision.objects.filter(assignment=assignment).count() == 1
    assert ActivityEvent.objects.count() == 0
    never_submitted.refresh_from_db()
    assert never_submitted.work_state == Assignment.WorkState.ASSIGNED
    assert not DraftCycle.objects.filter(assignment=never_submitted).exists()
    assert not AnnotationRevision.objects.filter(assignment=never_submitted).exists()
    preserved = CurrentDraft.objects.get(draft_cycle_id=second_saved["draft_cycle_id"])
    assert preserved.draft_version == 1
    assert preserved.state == canonical_for(assignment, changed_state)

    reopened = admin_client.post(f"{lifecycle_path}/reopen")
    reopened_again = admin_client.post(f"{lifecycle_path}/reopen")

    assert reopened.status_code == reopened_again.status_code == 200
    assert reopened.json()["status"] == WorkBatch.Status.OPEN
    resumed = request_json(
        client,
        "put",
        draft_path,
        {"expected_draft_version": 1, "state": complete_state(), "tab_id": tab_id},
    )
    assert resumed.status_code == 200
    assert resumed.json()["draft_version"] == 2
    opened_after_reopen = request_json(
        client,
        "post",
        f"/api/worker/assignments/{never_submitted.assignment_id}/open",
        {"tab_id": tab_id},
    )
    assert opened_after_reopen.status_code == 200
    assert opened_after_reopen.json()["work_state"] == Assignment.WorkState.IN_PROGRESS
    assert AnnotationRevision.objects.filter(assignment=assignment).count() == 1
