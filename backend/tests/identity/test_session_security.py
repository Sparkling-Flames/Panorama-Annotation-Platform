from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

pytestmark = pytest.mark.django_db

ADMIN_TEST_PASSPHRASE = "admin-session-security-passphrase"
INITIAL_WORKER_PASSWORD = "worker-initial-security-passphrase"
CHANGED_WORKER_PASSWORD = "worker-changed-security-passphrase"
PROFILE_RANKING_KEYS = {
    "active_time",
    "lcb",
    "profile",
    "profile_score",
    "quality_score",
    "rank",
    "ranking",
    "score",
    "tier",
}


def create_administrator() -> Any:
    user_model = get_user_model()
    return user_model.objects.create_superuser(
        username="session-security-admin",
        password=ADMIN_TEST_PASSPHRASE,
    )


def create_worker(admin_client: Client) -> dict[str, Any]:
    response = admin_client.post(
        "/api/admin/workers",
        {"username": "session-security-worker"},
        content_type="application/json",
    )

    assert response.status_code == 201
    return response.json()


def login(client: Client, *, username: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/api/auth/login",
        {"username": username, "password": password},
        content_type="application/json",
    )

    assert response.status_code == 200
    return response.json()


def change_password(client: Client, *, current_password: str, new_password: str) -> None:
    response = client.post(
        "/api/auth/change-password",
        {"current_password": current_password, "new_password": new_password},
        content_type="application/json",
    )

    assert response.status_code == 204


def assert_without_profile_rankings(value: object) -> None:
    if isinstance(value, Mapping):
        assert PROFILE_RANKING_KEYS.isdisjoint(value)
        for nested_value in value.values():
            assert_without_profile_rankings(nested_value)
    elif isinstance(value, list):
        for nested_value in value:
            assert_without_profile_rankings(nested_value)


def test_current_session_exposes_only_the_role_needed_for_route_visibility() -> None:
    anonymous_response = Client().get("/api/auth/session")
    assert anonymous_response.status_code == 200
    assert anonymous_response.json() == {"authenticated": False}

    administrator = create_administrator()
    admin_client = Client()
    admin_client.force_login(administrator)
    admin_payload = admin_client.get("/api/auth/session").json()
    assert admin_payload == {
        "authenticated": True,
        "must_change_password": False,
        "role": "admin",
    }
    assert_without_profile_rankings(admin_payload)


def test_password_change_and_account_disable_revoke_existing_worker_sessions() -> None:
    administrator = create_administrator()
    admin_client = Client()
    admin_client.force_login(administrator)
    worker = create_worker(admin_client)

    first_session = Client()
    assert login(
        first_session,
        username=worker["username"],
        password=worker["temporary_password"],
    ) == {"must_change_password": True, "workspace_access": False}
    change_password(
        first_session,
        current_password=worker["temporary_password"],
        new_password=INITIAL_WORKER_PASSWORD,
    )
    notice = first_session.get("/api/privacy/notice").json()
    assert (
        first_session.post(
            "/api/privacy/notice/accept",
            data={"notice_version": notice["notice_version"]},
            content_type="application/json",
        ).status_code
        == 201
    )

    second_session = Client()
    assert login(
        second_session,
        username=worker["username"],
        password=INITIAL_WORKER_PASSWORD,
    ) == {"must_change_password": False, "workspace_access": True}

    change_password(
        first_session,
        current_password=INITIAL_WORKER_PASSWORD,
        new_password=CHANGED_WORKER_PASSWORD,
    )
    assert first_session.get("/api/workspace/session").status_code == 200
    assert second_session.get("/api/workspace/session").status_code == 401

    disable_response = admin_client.post(f"/api/admin/workers/{worker['worker_id']}/disable")
    assert disable_response.status_code == 204
    assert first_session.get("/api/workspace/session").status_code == 401

    assert (
        Client()
        .post(
            "/api/auth/login",
            {"username": worker["username"], "password": INITIAL_WORKER_PASSWORD},
            content_type="application/json",
        )
        .status_code
        == 401
    )
    assert (
        Client()
        .post(
            "/api/auth/login",
            {"username": worker["username"], "password": CHANGED_WORKER_PASSWORD},
            content_type="application/json",
        )
        .status_code
        == 401
    )


def test_pap_iam_sc_011_worker_responses_exclude_workspace_tokens_and_profile_rankings() -> None:
    administrator = create_administrator()
    admin_client = Client()
    admin_client.force_login(administrator)
    worker = create_worker(admin_client)
    worker_client = Client()

    login_payload = login(
        worker_client,
        username=worker["username"],
        password=worker["temporary_password"],
    )
    assert login_payload == {"must_change_password": True, "workspace_access": False}
    assert_without_profile_rankings(login_payload)

    change_password(
        worker_client,
        current_password=worker["temporary_password"],
        new_password=INITIAL_WORKER_PASSWORD,
    )
    workspace_session = worker_client.get("/api/workspace/session")
    assert workspace_session.status_code == 200
    assert workspace_session.json() == {"workspace_access": False}
    assert_without_profile_rankings(workspace_session.json())

    notice = worker_client.get("/api/privacy/notice").json()
    accepted = worker_client.post(
        "/api/privacy/notice/accept",
        data={"notice_version": notice["notice_version"]},
        content_type="application/json",
    )
    assert accepted.status_code == 201
    assert worker_client.get("/api/workspace/session").json() == {"workspace_access": True}

    acquire_response = worker_client.post(
        "/api/workspace/acquire",
        {
            "client_instance_id": str(uuid4()),
            "tab_id": str(uuid4()),
            "takeover": False,
        },
        content_type="application/json",
    )
    assert acquire_response.status_code == 201
    assert set(acquire_response.json()) == {"lease_expires_at", "workspace_state"}
    assert "token" not in acquire_response.content.decode("utf-8").lower()
    assert_without_profile_rankings(acquire_response.json())
    assert "active_workspace_token" in worker_client.session

    forbidden_admin_response = worker_client.get(f"/api/admin/workers/{worker['worker_id']}")
    assert forbidden_admin_response.status_code == 403
    assert forbidden_admin_response.json() == {"error": {"code": "admin_required"}}
    assert_without_profile_rankings(forbidden_admin_response.json())
