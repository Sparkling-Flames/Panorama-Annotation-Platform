from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

pytestmark = pytest.mark.django_db


def create_account(*, username: str, role: str) -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        username=username,
        role=role,
        must_change_password=False,
    )


def signed_in_client(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def acquire_workspace(
    client: Client,
    *,
    client_instance_id: str,
    tab_id: str,
    takeover: bool = False,
):
    return client.post(
        "/api/workspace/acquire",
        {
            "client_instance_id": client_instance_id,
            "tab_id": tab_id,
            "takeover": takeover,
        },
        content_type="application/json",
    )


def renew_workspace(client: Client, *, tab_id: str):
    return client.post(
        "/api/workspace/renew",
        {"tab_id": tab_id},
        content_type="application/json",
    )


def test_workspace_primitive_rotates_lease_on_explicit_second_device_takeover() -> None:
    worker = create_account(username="lease-worker", role="worker")
    first_device = signed_in_client(worker)
    second_device = signed_in_client(worker)
    first_tab_id = str(uuid4())
    second_tab_id = str(uuid4())

    first_response = acquire_workspace(
        first_device,
        client_instance_id=str(uuid4()),
        tab_id=first_tab_id,
    )
    assert first_response.status_code == 201
    assert set(first_response.json()) == {"lease_expires_at", "workspace_state"}
    assert first_response.json()["workspace_state"] == "editable"
    assert "token" not in first_response.content.decode("utf-8").lower()

    workspace_model = apps.get_model("identity", "ActiveWorkspace")
    initial_workspace = workspace_model.objects.get(worker=worker)
    initial_token = initial_workspace.token
    initial_session_token = first_device.session["active_workspace_token"]

    conflict_response = acquire_workspace(
        second_device,
        client_instance_id=str(uuid4()),
        tab_id=second_tab_id,
    )
    assert conflict_response.status_code == 409
    assert conflict_response.json() == {"error": {"code": "workspace_takeover_required"}}
    assert workspace_model.objects.filter(worker=worker).count() == 1
    assert workspace_model.objects.get(worker=worker).token == initial_token

    takeover_response = acquire_workspace(
        second_device,
        client_instance_id=str(uuid4()),
        tab_id=second_tab_id,
        takeover=True,
    )
    assert takeover_response.status_code == 201
    active_workspace = workspace_model.objects.get(worker=worker)
    assert active_workspace.token != initial_token
    assert second_device.session["active_workspace_token"] == str(active_workspace.token)
    assert first_device.session["active_workspace_token"] == initial_session_token

    expires_after_takeover = active_workspace.lease_expires_at
    rejected_renewal = renew_workspace(first_device, tab_id=first_tab_id)
    assert rejected_renewal.status_code == 409
    assert rejected_renewal.json() == {"error": {"code": "workspace_lease_lost"}}
    active_workspace.refresh_from_db()
    assert active_workspace.lease_expires_at == expires_after_takeover

    assert renew_workspace(second_device, tab_id=second_tab_id).status_code == 204


def test_workspace_primitive_rejects_a_second_tab_in_the_same_browser_session() -> None:
    worker = create_account(username="tab-worker", role="worker")
    browser = signed_in_client(worker)
    browser_instance_id = str(uuid4())
    first_tab_id = str(uuid4())
    second_tab_id = str(uuid4())

    assert (
        acquire_workspace(
            browser,
            client_instance_id=browser_instance_id,
            tab_id=first_tab_id,
        ).status_code
        == 201
    )

    second_tab_response = acquire_workspace(
        browser,
        client_instance_id=browser_instance_id,
        tab_id=second_tab_id,
        takeover=True,
    )
    assert second_tab_response.status_code == 409
    assert second_tab_response.json() == {"error": {"code": "workspace_tab_conflict"}}
    assert renew_workspace(browser, tab_id=second_tab_id).status_code == 409
    assert renew_workspace(browser, tab_id=first_tab_id).status_code == 204


def test_workspace_primitive_allows_expired_lease_reacquisition() -> None:
    worker = create_account(username="expired-lease-worker", role="worker")
    first_device = signed_in_client(worker)
    next_device = signed_in_client(worker)

    assert (
        acquire_workspace(
            first_device,
            client_instance_id=str(uuid4()),
            tab_id=str(uuid4()),
        ).status_code
        == 201
    )
    workspace_model = apps.get_model("identity", "ActiveWorkspace")
    workspace = workspace_model.objects.get(worker=worker)
    expired_token = workspace.token
    workspace.lease_expires_at = timezone.now() - timedelta(seconds=1)
    workspace.save(update_fields=["lease_expires_at"])

    reacquire_response = acquire_workspace(
        next_device,
        client_instance_id=str(uuid4()),
        tab_id=str(uuid4()),
    )
    assert reacquire_response.status_code == 201
    workspace.refresh_from_db()
    assert workspace.token != expired_token
    assert workspace_model.objects.filter(worker=worker).count() == 1


def test_admin_sessions_do_not_compete_for_worker_workspace() -> None:
    administrator = create_account(username="workspace-administrator", role="admin")
    first_admin_session = signed_in_client(administrator)
    second_admin_session = signed_in_client(administrator)

    for admin_session in (first_admin_session, second_admin_session):
        response = acquire_workspace(
            admin_session,
            client_instance_id=str(uuid4()),
            tab_id=str(uuid4()),
        )
        assert response.status_code == 200
        assert response.json() == {"workspace_required": False}

    workspace_model = apps.get_model("identity", "ActiveWorkspace")
    assert workspace_model.objects.count() == 0
