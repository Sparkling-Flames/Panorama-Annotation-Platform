from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from activity.models import ActivityEvent
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.test import Client
from identity.models import DataNoticeAcceptance, User
from identity.services import CURRENT_DATA_NOTICE_VERSION
from media.models import Asset, MediaVariant
from work.models import Assignment, OperationalIssue, Task
from work.services import assign_task, create_task_draft, create_work_batch, publish_task

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


def published_assignment(username: str) -> tuple[User, Assignment]:
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
    task = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=[variant],
            mode=Task.Mode.MANUAL,
            external_task_key=f"activity-{username}",
        ).task_id
    )
    return assigned_worker, assign_task(
        batch=create_work_batch(name=f"Activity {username}"),
        task=task,
        worker=assigned_worker,
    )


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


def open_draft(client: Client, assignment: Assignment, tab_id: str) -> dict[str, object]:
    opened = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/open",
        {"tab_id": tab_id},
    )
    assert opened.status_code == 200, opened.json()
    response = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/draft",
        {"tab_id": tab_id},
    )
    assert response.status_code == 200, response.json()
    return response.json()


def event_payload(
    *,
    assignment: Assignment,
    draft_cycle_id: str,
    tab_id: str,
    client_session_id: str,
    active_lease_id: str,
    sequence_no: int,
    event_type: str = "interaction",
    client_monotonic_ms: float = 0,
    client_wall_time_ms: int | None = None,
) -> dict[str, object]:
    return {
        "active_lease_id": active_lease_id,
        "active_time_rule_version": "active-time-v1",
        "assignment_id": str(assignment.assignment_id),
        "client_build_sha": "test-build",
        "client_monotonic_ms": client_monotonic_ms,
        "client_session_id": client_session_id,
        "client_wall_time_ms": (
            1_700_000_000_000 + int(client_monotonic_ms)
            if client_wall_time_ms is None
            else client_wall_time_ms
        ),
        "draft_cycle_id": draft_cycle_id,
        "event_id": str(uuid4()),
        "event_type": event_type,
        "focus": True,
        "interaction_type": "annotation_2d_edit" if event_type == "interaction" else None,
        "sequence_no": sequence_no,
        "tab_id": tab_id,
        "visibility": "visible",
    }


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


def test_pap_aof_sc_003_activity_event_replay_is_idempotent_and_strict() -> None:
    assigned_worker, assignment = published_assignment("activity-idempotent")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    draft = open_draft(client, assignment, tab_id)
    payload = event_payload(
        assignment=assignment,
        draft_cycle_id=draft["draft_cycle_id"],
        tab_id=tab_id,
        client_session_id=str(uuid4()),
        active_lease_id=str(uuid4()),
        sequence_no=1,
    )

    created = request_json(client, "post", "/api/worker/activity-events", payload)
    replayed = request_json(client, "post", "/api/worker/activity-events", payload)
    changed = request_json(
        client,
        "post",
        "/api/worker/activity-events",
        {**payload, "client_monotonic_ms": 1},
    )
    sequence_conflict_payload = {**payload, "event_id": str(uuid4())}
    sequence_conflict = request_json(
        client,
        "post",
        "/api/worker/activity-events",
        sequence_conflict_payload,
    )
    leaking = request_json(
        client,
        "post",
        "/api/worker/activity-events",
        {**payload, "event_id": str(uuid4()), "pointer_coordinates": [10, 20]},
    )
    invalid_interaction = request_json(
        client,
        "post",
        "/api/worker/activity-events",
        {**payload, "event_id": str(uuid4()), "interaction_type": []},
    )
    invalid_wall_clock = request_json(
        client,
        "post",
        "/api/worker/activity-events",
        {**payload, "client_wall_time_ms": "not-a-clock", "event_id": str(uuid4())},
    )
    missing_lease = request_json(
        client,
        "post",
        "/api/worker/activity-events",
        {**payload, "active_lease_id": None, "event_id": str(uuid4())},
    )

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json() == created.json()
    assert changed.status_code == 409
    assert changed.json() == {"error": {"code": "activity_event_conflict"}}
    assert sequence_conflict.status_code == 409
    assert sequence_conflict.json() == {"error": {"code": "activity_sequence_conflict"}}
    assert leaking.status_code == 400
    assert leaking.json() == {"error": {"code": "invalid_activity_event"}}
    assert invalid_interaction.status_code == 400
    assert invalid_interaction.json() == {"error": {"code": "invalid_activity_event"}}
    assert invalid_wall_clock.status_code == 400
    assert invalid_wall_clock.json() == {"error": {"code": "invalid_activity_event"}}
    assert missing_lease.status_code == 400
    assert missing_lease.json() == {"error": {"code": "invalid_activity_event"}}
    assert sorted(OperationalIssue.objects.values_list("error_code", flat=True)) == [
        "activity_event_conflict",
        "activity_sequence_conflict",
    ]
    assert ActivityEvent.objects.count() == 1
    event = ActivityEvent.objects.get()
    event.focus = False
    with pytest.raises(ValidationError):
        event.save()
    with pytest.raises(ValidationError):
        event.delete()
    with pytest.raises(DatabaseError), transaction.atomic():
        ActivityEvent.objects.filter(pk=event.pk).update(focus=False)
    with pytest.raises(DatabaseError), transaction.atomic():
        ActivityEvent.objects.filter(pk=event.pk).delete()


def test_pap_aof_sc_004_and_006_activity_is_capped_and_split_by_draft_cycle() -> None:
    # PAP-AOF-SC-006: Revision 1 and Revision 2 keep separate derived time buckets.
    assigned_worker, assignment = published_assignment("activity-summary")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    first_draft = open_draft(client, assignment, tab_id)
    first_session = str(uuid4())
    first_lease = str(uuid4())
    first_event = event_payload(
        assignment=assignment,
        draft_cycle_id=first_draft["draft_cycle_id"],
        tab_id=tab_id,
        client_session_id=first_session,
        active_lease_id=first_lease,
        sequence_no=1,
    )
    third_event = event_payload(
        assignment=assignment,
        draft_cycle_id=first_draft["draft_cycle_id"],
        tab_id=tab_id,
        client_session_id=first_session,
        active_lease_id=first_lease,
        sequence_no=3,
        event_type="idle",
        client_monotonic_ms=3_600_000,
    )
    second_event = event_payload(
        assignment=assignment,
        draft_cycle_id=first_draft["draft_cycle_id"],
        tab_id=tab_id,
        client_session_id=first_session,
        active_lease_id=first_lease,
        sequence_no=2,
        client_monotonic_ms=10_000,
    )
    for payload in (first_event, third_event, second_event):
        assert (
            request_json(client, "post", "/api/worker/activity-events", payload).status_code == 201
        )

    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    saved = request_json(
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
            "expected_state_sha": saved["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )
    assert submitted.status_code == 201
    assert (
        request_json(
            client,
            "post",
            f"/api/worker/assignments/{assignment.assignment_id}/revise",
            {"tab_id": tab_id},
        ).status_code
        == 201
    )
    second_draft = client.get(draft_path, {"tab_id": tab_id}).json()
    second_session = str(uuid4())
    second_lease = str(uuid4())
    for payload in (
        event_payload(
            assignment=assignment,
            draft_cycle_id=second_draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=second_session,
            active_lease_id=second_lease,
            sequence_no=1,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=second_draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=second_session,
            active_lease_id=second_lease,
            sequence_no=2,
            event_type="idle",
            client_monotonic_ms=5_000,
        ),
    ):
        assert (
            request_json(client, "post", "/api/worker/activity-events", payload).status_code == 201
        )

    before_second_submit = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/activity-summary"
    )
    assert before_second_submit.status_code == 200
    assert before_second_submit.json() == {
        "active_time_rule_version": "active-time-v1",
        "initial_ms": 25_000,
        "reasons": ["interval_capped"],
        "revision_ms": 0,
        "rework_ms": 0,
        "total_ms": 30_000,
        "unsubmitted_ms": 5_000,
    }

    second = request_json(
        client,
        "post",
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": second_draft["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )
    assert second.status_code == 201
    after_second_submit = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/activity-summary"
    )
    assert after_second_submit.json() == {
        **before_second_submit.json(),
        "revision_ms": 5_000,
        "unsubmitted_ms": 0,
    }


def test_activity_write_requires_owned_assignment_and_current_workspace() -> None:
    owner, assignment = published_assignment("activity-owner")
    owner_tab = str(uuid4())
    old_client = client_with_workspace(owner, owner_tab)
    draft = open_draft(old_client, assignment, owner_tab)
    payload = event_payload(
        assignment=assignment,
        draft_cycle_id=draft["draft_cycle_id"],
        tab_id=owner_tab,
        client_session_id=str(uuid4()),
        active_lease_id=str(uuid4()),
        sequence_no=1,
    )
    client_with_workspace(owner, str(uuid4()), takeover=True)
    lost = request_json(old_client, "post", "/api/worker/activity-events", payload)

    stranger = worker("activity-stranger")
    stranger_tab = str(uuid4())
    stranger_client = client_with_workspace(stranger, stranger_tab)
    foreign_payload = {**payload, "tab_id": stranger_tab}
    foreign = request_json(stranger_client, "post", "/api/worker/activity-events", foreign_payload)
    missing_payload = deepcopy(foreign_payload)
    missing_payload["assignment_id"] = str(uuid4())
    missing = request_json(stranger_client, "post", "/api/worker/activity-events", missing_payload)

    assert lost.status_code == 409
    assert lost.json() == {"error": {"code": "workspace_lease_lost"}}
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"error": {"code": "resource_not_found"}}
    assert ActivityEvent.objects.count() == 0
    assert list(OperationalIssue.objects.values_list("error_code", flat=True)) == [
        "workspace_lease_lost"
    ]


def test_overlapping_client_leases_are_not_double_counted() -> None:
    assigned_worker, assignment = published_assignment("activity-overlap")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    draft = open_draft(client, assignment, tab_id)
    client_session_id = str(uuid4())
    first_lease = str(uuid4())
    second_lease = str(uuid4())
    events = (
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=client_session_id,
            active_lease_id=first_lease,
            sequence_no=1,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=client_session_id,
            active_lease_id=second_lease,
            sequence_no=2,
            client_monotonic_ms=5_000,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=client_session_id,
            active_lease_id=first_lease,
            sequence_no=3,
            event_type="idle",
            client_monotonic_ms=10_000,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=client_session_id,
            active_lease_id=second_lease,
            sequence_no=4,
            event_type="idle",
            client_monotonic_ms=15_000,
        ),
    )
    for payload in events:
        assert (
            request_json(client, "post", "/api/worker/activity-events", payload).status_code == 201
        )

    summary = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/activity-summary"
    ).json()

    assert summary["unsubmitted_ms"] == 15_000
    assert summary["total_ms"] == 15_000
    assert summary["reasons"] == ["lease_switched"]


def test_pap_aof_sc_011_overlapping_sessions_are_unioned_by_client_wall_time() -> None:
    assigned_worker, assignment = published_assignment("activity-cross-session-overlap")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    draft = open_draft(client, assignment, tab_id)
    first_session = str(uuid4())
    second_session = str(uuid4())
    first_lease = str(uuid4())
    second_lease = str(uuid4())
    for payload in (
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=first_session,
            active_lease_id=first_lease,
            sequence_no=1,
            client_wall_time_ms=1_700_000_000_000,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=first_session,
            active_lease_id=first_lease,
            sequence_no=2,
            event_type="idle",
            client_monotonic_ms=10_000,
            client_wall_time_ms=1_700_000_010_000,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=second_session,
            active_lease_id=second_lease,
            sequence_no=1,
            client_wall_time_ms=1_700_000_005_000,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=second_session,
            active_lease_id=second_lease,
            sequence_no=2,
            event_type="idle",
            client_monotonic_ms=10_000,
            client_wall_time_ms=1_700_000_015_000,
        ),
    ):
        assert (
            request_json(client, "post", "/api/worker/activity-events", payload).status_code == 201
        )

    summary = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/activity-summary"
    ).json()

    assert summary["unsubmitted_ms"] == 15_000
    assert summary["total_ms"] == 15_000


def test_pap_aof_sc_012_published_task_freezes_active_time_v1() -> None:
    _, assignment = published_assignment("activity-rule-freeze")
    task = assignment.task

    assert task.active_time_rule_version == "active-time-v1"
    task.active_time_rule_version = "future-v2"
    with pytest.raises(ValidationError):
        task.save()
    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(active_time_rule_version="future-v2")


def test_backward_wall_clock_is_reported_and_never_increases_active_time() -> None:
    assigned_worker, assignment = published_assignment("activity-backward-wall")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    draft = open_draft(client, assignment, tab_id)
    session_id = str(uuid4())
    lease_id = str(uuid4())
    for payload in (
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=session_id,
            active_lease_id=lease_id,
            sequence_no=1,
            client_wall_time_ms=1_700_000_010_000,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=session_id,
            active_lease_id=lease_id,
            sequence_no=2,
            event_type="idle",
            client_monotonic_ms=10_000,
            client_wall_time_ms=1_700_000_000_000,
        ),
    ):
        assert (
            request_json(client, "post", "/api/worker/activity-events", payload).status_code == 201
        )

    summary = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/activity-summary"
    ).json()

    assert summary["total_ms"] == 0
    assert summary["reasons"] == ["wall_clock_non_monotonic"]


def test_pap_aof_sc_010_active_time_is_context_not_a_decision() -> None:
    assigned_worker, assignment = published_assignment("activity-context")
    tab_id = str(uuid4())
    client = client_with_workspace(assigned_worker, tab_id)
    draft = open_draft(client, assignment, tab_id)
    session_id = str(uuid4())
    lease_id = str(uuid4())
    for payload in (
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=session_id,
            active_lease_id=lease_id,
            sequence_no=1,
        ),
        event_payload(
            assignment=assignment,
            draft_cycle_id=draft["draft_cycle_id"],
            tab_id=tab_id,
            client_session_id=session_id,
            active_lease_id=lease_id,
            sequence_no=2,
            event_type="idle",
            client_monotonic_ms=1_000,
        ),
    ):
        assert (
            request_json(client, "post", "/api/worker/activity-events", payload).status_code == 201
        )

    summary = client.get(
        f"/api/worker/assignments/{assignment.assignment_id}/activity-summary"
    ).json()
    assignment.refresh_from_db()

    assert set(summary) == {
        "active_time_rule_version",
        "initial_ms",
        "reasons",
        "revision_ms",
        "rework_ms",
        "total_ms",
        "unsubmitted_ms",
    }
    assert summary["total_ms"] == 1_000
    assert assignment.work_state == Assignment.WorkState.IN_PROGRESS
    assert assignment.review_state == Assignment.ReviewState.UNREVIEWED
