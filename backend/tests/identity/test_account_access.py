from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import Client

pytestmark = pytest.mark.django_db
ADMIN_TEST_PASSPHRASE = "admin-" + "test-passphrase"


def create_administrator() -> Any:
    user_model = get_user_model()
    return user_model.objects.create_superuser(
        username="admin-operator",
        password=ADMIN_TEST_PASSPHRASE,
    )


def authenticated_administrator() -> Client:
    administrator = create_administrator()
    admin_client = Client()
    admin_client.force_login(administrator)
    return admin_client


def create_worker(admin_client: Client, username: str = "worker-login-001") -> dict[str, Any]:
    response = admin_client.post(
        "/api/admin/workers",
        {"username": username},
        content_type="application/json",
    )

    assert response.status_code == 201
    return response.json()


def finish_first_login(worker: dict[str, Any], new_password: str) -> Client:
    worker_client = Client()
    login_response = worker_client.post(
        "/api/auth/login",
        {
            "username": worker["username"],
            "password": worker["temporary_password"],
        },
        content_type="application/json",
    )
    assert login_response.status_code == 200

    change_response = worker_client.post(
        "/api/auth/change-password",
        {
            "current_password": worker["temporary_password"],
            "new_password": new_password,
        },
        content_type="application/json",
    )
    assert change_response.status_code == 204
    return worker_client


def test_pap_iam_sc_002_public_registration_is_not_available(client: Client) -> None:
    user_model = get_user_model()
    account_count = user_model.objects.count()

    response = client.post(
        "/api/auth/register",
        {"username": "public-worker", "password": "public-test-passphrase"},
        content_type="application/json",
    )

    assert response.status_code == 404
    assert user_model.objects.count() == account_count


def test_pap_iam_sc_001_admin_assigns_stable_non_pii_worker_id_and_audits_creation() -> None:
    admin_client = authenticated_administrator()

    worker_payload = create_worker(admin_client, username="worker-alice-login")
    worker_id = worker_payload["worker_id"]

    assert UUID(worker_id).version == 4
    assert "alice" not in worker_id.lower()
    assert worker_payload["role"] == "worker"
    assert worker_payload["must_change_password"] is True
    assert len(worker_payload["temporary_password"]) >= 16

    user_model = get_user_model()
    worker = user_model.objects.get(worker_id=worker_id)
    original_worker_id = worker.worker_id
    worker.username = "renamed-login"
    worker.save(update_fields=["username"])
    worker.refresh_from_db()
    assert worker.worker_id == original_worker_id

    rejected_id = str(uuid4())
    rejected_response = admin_client.post(
        "/api/admin/workers",
        {"username": "worker-chosen-id", "worker_id": rejected_id},
        content_type="application/json",
    )
    assert rejected_response.status_code == 400
    assert rejected_response.json()["error"]["code"] == "server_assigned_field"
    assert not user_model.objects.filter(worker_id=rejected_id).exists()

    audit_event = apps.get_model("identity", "AuditEvent").objects.get(
        action="worker.created",
        target_worker=worker,
    )
    assert audit_event.actor.role == "admin"
    assert worker_payload["temporary_password"] not in json.dumps(audit_event.details)


def test_pap_iam_sc_003_temporary_password_blocks_workspace_until_changed() -> None:
    worker = create_worker(authenticated_administrator())
    worker_client = Client()

    login_response = worker_client.post(
        "/api/auth/login",
        {"username": worker["username"], "password": worker["temporary_password"]},
        content_type="application/json",
    )
    assert login_response.status_code == 200
    assert login_response.json() == {
        "must_change_password": True,
        "workspace_access": False,
    }

    blocked_response = worker_client.get("/api/workspace/session")
    assert blocked_response.status_code == 403
    assert blocked_response.json()["error"]["code"] == "password_change_required"

    changed_response = worker_client.post(
        "/api/auth/change-password",
        {
            "current_password": worker["temporary_password"],
            "new_password": "worker-permanent-passphrase",
        },
        content_type="application/json",
    )
    assert changed_response.status_code == 204

    workspace_response = worker_client.get("/api/workspace/session")
    assert workspace_response.status_code == 200
    assert workspace_response.json() == {"workspace_access": False}

    notice = worker_client.get("/api/privacy/notice").json()
    accepted_response = worker_client.post(
        "/api/privacy/notice/accept",
        {"notice_version": notice["notice_version"]},
        content_type="application/json",
    )
    assert accepted_response.status_code == 201

    workspace_response = worker_client.get("/api/workspace/session")
    assert workspace_response.json() == {"workspace_access": True}


def test_pap_iam_sc_001_password_is_hashed_and_never_returned_by_admin_detail() -> None:
    admin_client = authenticated_administrator()
    worker_payload = create_worker(admin_client)

    user_model = get_user_model()
    worker = user_model.objects.get(worker_id=worker_payload["worker_id"])
    assert worker.password != worker_payload["temporary_password"]
    assert worker.check_password(worker_payload["temporary_password"])
    assert not {
        "current_password",
        "plaintext_password",
        "temporary_password",
    }.intersection(field.name for field in worker._meta.concrete_fields)

    response = admin_client.get(f"/api/admin/workers/{worker.worker_id}")
    assert response.status_code == 200
    assert not {
        "current_password",
        "password",
        "plaintext_password",
        "temporary_password",
    }.intersection(response.json())


def test_pap_iam_sc_004_password_reset_revokes_session_and_never_reveals_old_password() -> None:
    admin_client = authenticated_administrator()
    worker = create_worker(admin_client)
    old_password = "worker-old-permanent-passphrase"
    worker_client = finish_first_login(worker, old_password)
    assert worker_client.get("/api/workspace/session").status_code == 200

    reset_response = admin_client.post(f"/api/admin/workers/{worker['worker_id']}/reset-password")
    assert reset_response.status_code == 200
    reset_payload = reset_response.json()
    assert set(reset_payload) == {"must_change_password", "temporary_password"}
    assert reset_payload["must_change_password"] is True
    assert reset_payload["temporary_password"] != old_password
    assert old_password not in reset_response.content.decode("utf-8")

    assert worker_client.get("/api/workspace/session").status_code == 401
    rejected_login = Client().post(
        "/api/auth/login",
        {"username": worker["username"], "password": old_password},
        content_type="application/json",
    )
    assert rejected_login.status_code == 401

    temporary_login = Client().post(
        "/api/auth/login",
        {
            "username": worker["username"],
            "password": reset_payload["temporary_password"],
        },
        content_type="application/json",
    )
    assert temporary_login.status_code == 200
    assert temporary_login.json()["must_change_password"] is True

    audit_event = apps.get_model("identity", "AuditEvent").objects.get(
        action="worker.password_reset",
        target_worker__worker_id=worker["worker_id"],
    )
    assert old_password not in json.dumps(audit_event.details)
    assert reset_payload["temporary_password"] not in json.dumps(audit_event.details)


def test_session_control_primitive_can_revoke_or_disable_without_deleting_account() -> None:
    admin_client = authenticated_administrator()
    worker = create_worker(admin_client)
    permanent_password = "worker-active-passphrase"
    worker_client = finish_first_login(worker, permanent_password)

    revoke_response = admin_client.post(f"/api/admin/workers/{worker['worker_id']}/revoke-sessions")
    assert revoke_response.status_code == 204
    assert worker_client.get("/api/workspace/session").status_code == 401

    relogged_client = Client()
    assert (
        relogged_client.post(
            "/api/auth/login",
            {"username": worker["username"], "password": permanent_password},
            content_type="application/json",
        ).status_code
        == 200
    )

    disable_response = admin_client.post(f"/api/admin/workers/{worker['worker_id']}/disable")
    assert disable_response.status_code == 204
    assert relogged_client.get("/api/workspace/session").status_code == 401
    assert (
        Client()
        .post(
            "/api/auth/login",
            {"username": worker["username"], "password": permanent_password},
            content_type="application/json",
        )
        .status_code
        == 401
    )

    restore_response = admin_client.post(f"/api/admin/workers/{worker['worker_id']}/restore")
    assert restore_response.status_code == 204
    assert (
        Client()
        .post(
            "/api/auth/login",
            {"username": worker["username"], "password": permanent_password},
            content_type="application/json",
        )
        .status_code
        == 200
    )

    user_model = get_user_model()
    assert user_model.objects.filter(worker_id=worker["worker_id"]).count() == 1
    actions = set(
        apps.get_model("identity", "AuditEvent")
        .objects.filter(target_worker__worker_id=worker["worker_id"])
        .values_list("action", flat=True)
    )
    assert {"worker.sessions_revoked", "worker.disabled", "worker.restored"} <= actions
