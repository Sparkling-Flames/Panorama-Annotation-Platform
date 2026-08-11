from __future__ import annotations

import json
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from identity.models import AuditEvent, DataNoticeAcceptance, User
from identity.services import CURRENT_DATA_NOTICE_VERSION
from media.models import Asset, MediaVariant
from work.jobs import eligible_task_input_manifest, process_next_analysis_job
from work.models import AnnotationRevision, Assignment, GuidanceEvent, Task
from work.services import assign_task, create_task_draft, create_work_batch, publish_task

pytestmark = pytest.mark.django_db
SUBMISSION_VERSIONS = {
    "client_build_sha": "test-client-build",
    "interaction_contract_version": "annotation-interaction-v1",
    "viewer_version": "annotation-viewer-v1",
}


def post_json(client: Client, path: str, payload: dict[str, object]):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


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
                    "u": 0.2,
                    "v": 0.9,
                },
                "order_index": 0,
                "pair_id": pair_id,
                "top": {
                    "point_id": "00000000-0000-4000-8000-000000000002",
                    "u": 0.2,
                    "v": 0.1,
                },
            }
        ],
        "portals": [],
        "seam_anchor_pair_id": pair_id,
        "scope_reason_codes": [],
        "scope_reason_text": "",
        "worker_scope_observation": "annotatable",
    }


def assigned_workers() -> tuple[User, User, Assignment, Assignment]:
    asset = Asset.objects.create(source_key="guidance/source")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key="cos://guidance/compressed.jpg",
        object_version="guidance-v1",
        content_sha256="a" * 64,
        content_length=100,
        content_crc64ecma="123",
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
            external_task_key="guidance-task",
            media_variants=(variant,),
            mode=Task.Mode.MANUAL,
        ).task_id
    )
    batch = create_work_batch(name="Guidance batch")
    first = User.objects.create_user(
        username="guidance-first", role=User.Role.WORKER, must_change_password=False
    )
    second = User.objects.create_user(
        username="guidance-second", role=User.Role.WORKER, must_change_password=False
    )
    return (
        first,
        second,
        assign_task(batch=batch, task=task, worker=first),
        assign_task(batch=batch, task=task, worker=second),
    )


def worker_client(worker: User) -> tuple[Client, str]:
    DataNoticeAcceptance.objects.get_or_create(
        worker=worker, notice_version=CURRENT_DATA_NOTICE_VERSION
    )
    client = Client()
    client.force_login(worker)
    tab_id = str(uuid4())
    acquired = post_json(
        client,
        "/api/workspace/acquire",
        {"client_instance_id": str(uuid4()), "tab_id": tab_id, "takeover": False},
    )
    assert acquired.status_code == 201
    return client, tab_id


def submit(client: Client, assignment: Assignment, tab_id: str) -> AnnotationRevision:
    assert (
        post_json(
            client,
            f"/api/worker/assignments/{assignment.assignment_id}/open",
            {"tab_id": tab_id},
        ).status_code
        == 200
    )
    draft_path = f"/api/worker/assignments/{assignment.assignment_id}/draft"
    assert client.get(draft_path, {"tab_id": tab_id}).status_code == 200
    saved = client.put(
        draft_path,
        data=json.dumps({"expected_draft_version": 0, "state": complete_state(), "tab_id": tab_id}),
        content_type="application/json",
    )
    assert saved.status_code == 200
    response = post_json(
        client,
        f"/api/worker/assignments/{assignment.assignment_id}/submit",
        {
            **SUBMISSION_VERSIONS,
            "expected_state_sha": saved.json()["state_sha"],
            "idempotency_key": str(uuid4()),
            "locale": "zh-CN",
            "tab_id": tab_id,
        },
    )
    assert response.status_code == 201
    return AnnotationRevision.objects.get(revision_id=response.json()["revision_id"])


def test_pap_aae_sc_007_guidance_is_private_and_only_acknowledgement_marks_feedback() -> None:
    first, second, first_assignment, second_assignment = assigned_workers()
    administrator = User.objects.create_user(
        username="guidance-admin", role=User.Role.ADMIN, must_change_password=False
    )
    admin = Client()
    admin.force_login(administrator)
    first_client, first_tab = worker_client(first)
    second_client, second_tab = worker_client(second)

    first_created = post_json(
        admin,
        f"/api/admin/assignments/{first_assignment.assignment_id}/guidance-events",
        {
            "category": "geometry_hint",
            "channel": "wechat",
            "revision_id": None,
            "summary": "请重新检查门洞四角。",
        },
    )
    second_created = post_json(
        admin,
        f"/api/admin/assignments/{second_assignment.assignment_id}/guidance-events",
        {
            "category": "geometry_hint",
            "channel": "platform",
            "revision_id": None,
            "summary": "请确认墙面上下点对。",
        },
    )
    assert first_created.status_code == second_created.status_code == 201
    first_id = first_created.json()["guidance_id"]
    second_id = second_created.json()["guidance_id"]
    assert first_created.json() == {
        "acknowledged_at": None,
        "administrator_id": str(administrator.worker_id),
        "assignment_id": str(first_assignment.assignment_id),
        "batch_id": str(first_assignment.batch_id),
        "category": "geometry_hint",
        "channel": "wechat",
        "created_at": first_created.json()["created_at"],
        "feedback_draft_cycle_id": None,
        "guidance_id": first_id,
        "revision_id": None,
        "summary": "请重新检查门洞四角。",
        "task_id": str(first_assignment.task_id),
        "worker_id": str(first.worker_id),
    }
    assert AuditEvent.objects.filter(
        action="guidance.created", target_id=first_id, target_worker=first
    ).exists()
    assert (
        post_json(
            first_client,
            f"/api/admin/assignments/{first_assignment.assignment_id}/guidance-events",
            {
                "category": "geometry_hint",
                "channel": "platform",
                "revision_id": None,
                "summary": "A worker cannot publish guidance.",
            },
        ).status_code
        == 403
    )

    assert [
        item["guidance_id"]
        for item in first_client.get("/api/worker/guidance-events").json()["events"]
    ] == [first_id]
    assert [
        item["guidance_id"]
        for item in second_client.get("/api/worker/guidance-events").json()["events"]
    ] == [second_id]
    assert (
        post_json(
            second_client, f"/api/worker/guidance-events/{first_id}/acknowledge", {}
        ).status_code
        == 404
    )
    assert (
        post_json(
            first_client,
            f"/api/worker/guidance-events/{first_id}/acknowledge",
            {"reply": "This endpoint must not become a chat thread."},
        ).status_code
        == 400
    )

    acknowledged = post_json(
        second_client,
        f"/api/worker/guidance-events/{second_id}/acknowledge",
        {},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["acknowledged_at"] is not None
    assert acknowledged.json()["feedback_draft_cycle_id"] is None

    first_revision = submit(first_client, first_assignment, first_tab)
    second_revision = submit(second_client, second_assignment, second_tab)
    first_revision_payload = admin.get(f"/api/admin/revisions/{first_revision.revision_id}").json()
    second_revision_payload = admin.get(
        f"/api/admin/revisions/{second_revision.revision_id}"
    ).json()
    assert first_revision_payload["feedback_exposed"] is False
    assert second_revision_payload["feedback_exposed"] is True
    assert (
        GuidanceEvent.objects.get(guidance_id=second_id).feedback_draft_cycle_id
        == second_revision.draft_cycle_id
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        GuidanceEvent.objects.filter(guidance_id=first_id).update(
            feedback_draft_cycle=first_revision.draft_cycle
        )

    assert (
        post_json(
            admin,
            f"/api/admin/assignments/{second_assignment.assignment_id}/guidance-events",
            {
                "category": "scope_hint",
                "channel": "platform",
                "revision_id": str(first_revision.revision_id),
                "summary": "This revision belongs to another Assignment.",
            },
        ).status_code
        == 404
    )

    while process_next_analysis_job(worker_id="guidance-assessment") is not None:
        pass
    manifest = eligible_task_input_manifest(
        task_id=first_assignment.task_id,
        batch_id=first_assignment.batch_id,
    )
    assert [item["revision_id"] for item in manifest["inputs"]] == [str(first_revision.revision_id)]
