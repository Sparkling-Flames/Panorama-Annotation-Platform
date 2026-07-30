from __future__ import annotations

import json
import secrets

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

pytestmark = pytest.mark.django_db


def test_browser_must_bootstrap_csrf_before_login() -> None:
    password = secrets.token_urlsafe(18)
    get_user_model().objects.create_superuser(username="csrf-admin", password=password)
    client = Client(enforce_csrf_checks=True)
    login_body = json.dumps({"username": "csrf-admin", "password": password})

    assert (
        client.post("/api/auth/login", data=login_body, content_type="application/json").status_code
        == 403
    )

    bootstrap = client.get("/api/auth/csrf")

    assert bootstrap.status_code == 200
    csrf_token = client.cookies["csrftoken"].value
    login = client.post(
        "/api/auth/login",
        data=login_body,
        content_type="application/json",
        headers={"X-CSRFToken": csrf_token},
    )
    assert login.status_code == 200
