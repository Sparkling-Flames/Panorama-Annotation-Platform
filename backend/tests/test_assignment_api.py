from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from identity.models import User
from identity.services import revoke_worker_sessions
from media.models import Asset, MediaVariant
from work.models import Assignment, Task
from work.services import (
    assign_task,
    cancel_task,
    create_task_draft,
    create_work_batch,
    publish_task,
    supersede_task,
)

pytestmark = pytest.mark.django_db


def published_task(suffix: str) -> Task:
    asset = Asset.objects.create(source_key=f"panoramas/assignment-{suffix}")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key=f"cos://assignment-{suffix}/compressed",
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
    draft = create_task_draft(
        asset=asset,
        media_variants=[variant],
        mode=Task.Mode.MANUAL,
        external_task_key=f"external-{suffix}",
    )
    return publish_task(task_id=draft.task_id)


def worker(username: str) -> User:
    return User.objects.create_user(
        username=username,
        password=None,
        role=User.Role.WORKER,
        must_change_password=False,
    )


def logged_in(worker_user: User) -> Client:
    client = Client()
    client.force_login(worker_user)
    return client


def acquire_workspace(client: Client, *, tab_id: str, takeover: bool = False) -> None:
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


def post_json(client: Client, path: str, payload: dict[str, object]):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def test_task_4_3_worker_switches_queue_states_and_opens_multiple_assignments() -> None:
    assigned_worker = worker("batch-worker")
    foreign_worker = worker("foreign-batch-worker")
    batch = create_work_batch(name="Warehouse round")
    first = assign_task(batch=batch, task=published_task("first"), worker=assigned_worker)
    second = assign_task(batch=batch, task=published_task("second"), worker=assigned_worker)
    foreign = assign_task(batch=batch, task=published_task("foreign"), worker=foreign_worker)
    client = logged_in(assigned_worker)
    tab_id = str(uuid4())
    acquire_workspace(client, tab_id=tab_id)

    list_response = client.get(f"/api/worker/batches/{batch.batch_id}/assignments")

    assert list_response.status_code == 200
    assignments = list_response.json()["assignments"]
    assert {item["assignment_id"] for item in assignments} == {
        str(first.assignment_id),
        str(second.assignment_id),
    }
    assert str(foreign.assignment_id) not in list_response.content.decode()

    first_open = post_json(
        client,
        f"/api/worker/assignments/{first.assignment_id}/open",
        {"tab_id": tab_id},
    )
    deferred = post_json(
        client,
        f"/api/worker/assignments/{first.assignment_id}/queue-state",
        {"queue_state": "deferred", "tab_id": tab_id},
    )
    second_open = post_json(
        client,
        f"/api/worker/assignments/{second.assignment_id}/open",
        {"tab_id": tab_id},
    )
    needs_revisit = post_json(
        client,
        f"/api/worker/assignments/{first.assignment_id}/queue-state",
        {"queue_state": "needs_revisit", "tab_id": tab_id},
    )
    ready = post_json(
        client,
        f"/api/worker/assignments/{first.assignment_id}/queue-state",
        {"queue_state": "ready", "tab_id": tab_id},
    )

    assert (
        first_open.status_code
        == deferred.status_code
        == second_open.status_code
        == needs_revisit.status_code
        == ready.status_code
        == 200
    )
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.queue_state == Assignment.QueueState.READY
    assert first.work_state == second.work_state == Assignment.WorkState.IN_PROGRESS


def test_task_4_3_worker_can_read_an_owned_assignment_only() -> None:
    assigned_worker = worker("return-worker")
    another_worker = worker("other-return-worker")
    batch = create_work_batch(name="Return batch")
    own = assign_task(batch=batch, task=published_task("return-own"), worker=assigned_worker)
    foreign = assign_task(
        batch=batch,
        task=published_task("return-foreign"),
        worker=another_worker,
    )
    client = logged_in(assigned_worker)

    own_response = client.get(f"/api/worker/assignments/{own.assignment_id}")
    foreign_response = client.get(f"/api/worker/assignments/{foreign.assignment_id}")
    missing_response = client.get(f"/api/worker/assignments/{uuid4()}")

    assert own_response.status_code == 200
    assert own_response.json()["assignment_id"] == str(own.assignment_id)
    assert own_response.json()["task"]["external_task_key"] == "external-return-own"
    assert foreign_response.status_code == missing_response.status_code == 404
    assert (
        foreign_response.json()
        == missing_response.json()
        == {"error": {"code": "resource_not_found"}}
    )


def test_assignment_write_rejects_a_workspace_that_was_taken_over() -> None:
    assigned_worker = worker("takeover-assignment-worker")
    batch = create_work_batch(name="Takeover batch")
    assignment = assign_task(
        batch=batch,
        task=published_task("takeover-assignment"),
        worker=assigned_worker,
    )
    old_device = logged_in(assigned_worker)
    new_device = logged_in(assigned_worker)
    old_tab_id = str(uuid4())
    new_tab_id = str(uuid4())
    acquire_workspace(old_device, tab_id=old_tab_id)
    acquire_workspace(new_device, tab_id=new_tab_id, takeover=True)

    rejected = post_json(
        old_device,
        f"/api/worker/assignments/{assignment.assignment_id}/open",
        {"tab_id": old_tab_id},
    )

    assert rejected.status_code == 409
    assert rejected.json() == {"error": {"code": "workspace_lease_lost"}}
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.ASSIGNED

    accepted = post_json(
        new_device,
        f"/api/worker/assignments/{assignment.assignment_id}/open",
        {"tab_id": new_tab_id},
    )
    assert accepted.status_code == 200


def test_revoked_session_cannot_read_worker_assignments() -> None:
    administrator = User.objects.create_user(
        username="session-revocation-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    assigned_worker = worker("revoked-assignment-worker")
    batch = create_work_batch(name="Revoked session batch")
    assign_task(batch=batch, task=published_task("revoked-session"), worker=assigned_worker)
    client = logged_in(assigned_worker)
    session_key = client.session.session_key
    assert session_key is not None and Session.objects.filter(session_key=session_key).exists()

    assert client.get(f"/api/worker/batches/{batch.batch_id}/assignments").status_code == 200
    revoke_worker_sessions(actor=administrator, worker=assigned_worker)

    response = client.get(f"/api/worker/batches/{batch.batch_id}/assignments")
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "authentication_required"}}


def test_administrator_creates_a_real_batch_assignment_contract() -> None:
    administrator = User.objects.create_user(
        username="assignment-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    assigned_worker = worker("admin-assigned-worker")
    task = published_task("admin-assigned")
    client = logged_in(administrator)

    batch_response = post_json(client, "/api/admin/work-batches", {"name": "Admin batch"})
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]

    assignment_response = post_json(
        client,
        f"/api/admin/work-batches/{batch_id}/assignments",
        {"task_id": str(task.task_id), "worker_id": str(assigned_worker.worker_id)},
    )

    assert assignment_response.status_code == 201
    assignment = Assignment.objects.get(assignment_id=assignment_response.json()["assignment_id"])
    assert assignment.batch_id == UUID(batch_id)
    assert assignment.task == task
    assert assignment.worker == assigned_worker


def test_task_4_5_open_changes_work_state_without_touching_review_state() -> None:
    assigned_worker = worker("independent-state-worker")
    batch = create_work_batch(name="Independent state batch")
    assignment = assign_task(
        batch=batch,
        task=published_task("independent-state"),
        worker=assigned_worker,
    )
    client = logged_in(assigned_worker)
    tab_id = str(uuid4())
    acquire_workspace(client, tab_id=tab_id)

    first_open = post_json(
        client,
        f"/api/worker/assignments/{assignment.assignment_id}/open",
        {"tab_id": tab_id},
    )
    retry = post_json(
        client,
        f"/api/worker/assignments/{assignment.assignment_id}/open",
        {"tab_id": tab_id},
    )

    assert first_open.status_code == retry.status_code == 200
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.IN_PROGRESS
    assert assignment.review_state == Assignment.ReviewState.UNREVIEWED


def test_task_4_5_same_task_isolated_per_worker_and_duplicate_exposure_conflicts() -> None:
    administrator = User.objects.create_user(
        username="independent-workers-admin",
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    first_worker = worker("independent-worker-one")
    second_worker = worker("independent-worker-two")
    task = published_task("independent-workers")
    first_batch = create_work_batch(name="Independent workers batch")
    second_batch = create_work_batch(name="Duplicate exposure batch")
    admin_client = logged_in(administrator)

    first_response = post_json(
        admin_client,
        f"/api/admin/work-batches/{first_batch.batch_id}/assignments",
        {"task_id": str(task.task_id), "worker_id": str(first_worker.worker_id)},
    )
    second_response = post_json(
        admin_client,
        f"/api/admin/work-batches/{first_batch.batch_id}/assignments",
        {"task_id": str(task.task_id), "worker_id": str(second_worker.worker_id)},
    )
    duplicate_response = post_json(
        admin_client,
        f"/api/admin/work-batches/{second_batch.batch_id}/assignments",
        {"task_id": str(task.task_id), "worker_id": str(first_worker.worker_id)},
    )

    assert first_response.status_code == second_response.status_code == 201
    assert first_response.json()["assignment_id"] != second_response.json()["assignment_id"]
    assert duplicate_response.status_code == 409
    assert duplicate_response.json() == {"error": {"code": "assignment_conflict"}}
    assert Assignment.objects.filter(task=task).count() == 2

    first_list = logged_in(first_worker).get(
        f"/api/worker/batches/{first_batch.batch_id}/assignments"
    )
    second_list = logged_in(second_worker).get(
        f"/api/worker/batches/{first_batch.batch_id}/assignments"
    )
    assert first_list.status_code == second_list.status_code == 200
    assert [item["assignment_id"] for item in first_list.json()["assignments"]] == [
        first_response.json()["assignment_id"]
    ]
    assert [item["assignment_id"] for item in second_list.json()["assignments"]] == [
        second_response.json()["assignment_id"]
    ]


def test_task_4_6_terminal_tasks_reject_worker_writes() -> None:
    assigned_worker = worker("terminal-task-worker")
    batch = create_work_batch(name="Terminal task batch")
    cancelled_task = published_task("terminal-cancelled")
    superseded_task = published_task("terminal-superseded")
    replacement_task = published_task("terminal-replacement")
    cancelled_assignment = assign_task(
        batch=batch,
        task=cancelled_task,
        worker=assigned_worker,
    )
    superseded_assignment = assign_task(
        batch=batch,
        task=superseded_task,
        worker=assigned_worker,
    )
    client = logged_in(assigned_worker)
    tab_id = str(uuid4())
    acquire_workspace(client, tab_id=tab_id)
    assert (
        post_json(
            client,
            f"/api/worker/assignments/{superseded_assignment.assignment_id}/open",
            {"tab_id": tab_id},
        ).status_code
        == 200
    )

    cancel_task(task_id=cancelled_task.task_id, reason="contract withdrawn")
    supersede_task(
        task_id=superseded_task.task_id,
        replacement_task_id=replacement_task.task_id,
        reason="replacement contract",
    )
    rejected_open = post_json(
        client,
        f"/api/worker/assignments/{cancelled_assignment.assignment_id}/open",
        {"tab_id": tab_id},
    )
    rejected_queue_change = post_json(
        client,
        f"/api/worker/assignments/{superseded_assignment.assignment_id}/queue-state",
        {"queue_state": "deferred", "tab_id": tab_id},
    )

    assert rejected_open.status_code == rejected_queue_change.status_code == 409
    assert rejected_open.json() == rejected_queue_change.json() == {
        "error": {"code": "assignment_task_unavailable"}
    }
    cancelled_assignment.refresh_from_db()
    superseded_assignment.refresh_from_db()
    assert cancelled_assignment.work_state == Assignment.WorkState.REVOKED
    assert superseded_assignment.work_state == Assignment.WorkState.REVOKED
    assert cancelled_assignment.queue_state == Assignment.QueueState.READY
    assert superseded_assignment.queue_state == Assignment.QueueState.READY
