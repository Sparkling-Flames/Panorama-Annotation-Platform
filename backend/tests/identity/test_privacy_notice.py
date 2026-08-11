from __future__ import annotations

import json
from uuid import uuid4

import pytest
from django.test import Client
from identity.models import AuditEvent, User

pytestmark = pytest.mark.django_db


def post_json(client: Client, path: str, payload: dict[str, object]):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def test_pap_pbd_sc_004_current_notice_is_required_before_workspace_access() -> None:
    worker = User.objects.create_user(
        username="privacy-worker",
        password=None,
        role=User.Role.WORKER,
        must_change_password=False,
    )
    client = Client()
    client.force_login(worker)

    notice = client.get("/api/privacy/notice")
    blocked = post_json(
        client,
        "/api/workspace/acquire",
        {
            "client_instance_id": str(uuid4()),
            "tab_id": str(uuid4()),
            "takeover": False,
        },
    )

    assert notice.status_code == 200
    payload = notice.json()
    assert payload["accepted"] is False
    assert payload["notice_version"] == "data-notice-v1"
    assert set(payload["copy"]) == {"en", "zh-CN"}
    assert payload["collected_data"] == [
        "account_and_worker_id",
        "assignment_draft_revision_review",
        "active_time_coarse_events",
        "errors_and_client_preflight",
        "guidance_acknowledgements",
    ]
    assert all(
        forbidden not in json.dumps(payload).lower()
        for forbidden in ("clipboard", "camera", "keystroke", "pointer trail", "screen recording")
    )
    assert blocked.status_code == 409
    assert blocked.json() == {"error": {"code": "notice_acceptance_required"}}

    invalid = post_json(
        client,
        "/api/privacy/notice/accept",
        {"notice_version": payload["notice_version"], "ip": "203.0.113.10"},
    )
    accepted = post_json(
        client,
        "/api/privacy/notice/accept",
        {"notice_version": payload["notice_version"]},
    )
    repeated = post_json(
        client,
        "/api/privacy/notice/accept",
        {"notice_version": payload["notice_version"]},
    )

    assert invalid.status_code == 400
    assert invalid.json() == {"error": {"code": "invalid_notice_acceptance"}}
    assert accepted.status_code == 201
    assert repeated.status_code == 200
    assert repeated.json() == accepted.json()
    assert accepted.json()["accepted_at"].endswith("Z")
    assert (
        AuditEvent.objects.filter(
            actor=worker,
            action="privacy.notice_accepted",
            details={"notice_version": "data-notice-v1"},
        ).count()
        == 1
    )

    allowed = post_json(
        client,
        "/api/workspace/acquire",
        {
            "client_instance_id": str(uuid4()),
            "tab_id": str(uuid4()),
            "takeover": False,
        },
    )
    assert allowed.status_code == 201


def test_old_notice_acceptance_does_not_satisfy_the_current_version() -> None:
    from identity.models import DataNoticeAcceptance

    worker = User.objects.create_user(
        username="privacy-reconfirm-worker",
        password=None,
        role=User.Role.WORKER,
        must_change_password=False,
    )
    DataNoticeAcceptance.objects.create(worker=worker, notice_version="data-notice-v0")
    client = Client()
    client.force_login(worker)

    response = client.get("/api/privacy/notice")

    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["notice_version"] == "data-notice-v1"
