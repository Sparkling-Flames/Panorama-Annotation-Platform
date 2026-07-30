from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


@dataclass(frozen=True)
class FakeOwnedResource:
    resource_id: str
    owner_worker_id: UUID


def authorization_api() -> Any:
    return importlib.import_module("identity.authorization")


def create_user(*, username: str, role: str) -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        username=username,
        role=role,
        must_change_password=False,
    )


@pytest.mark.parametrize("resource_kind", ["assignment", "revision", "media"])
def test_authorization_primitive_resolves_only_owned_resources_without_existence_leak(
    resource_kind: str,
) -> None:
    authorization = authorization_api()
    worker = create_user(username="assigned-worker", role="worker")
    another_worker = create_user(username="another-worker", role="worker")
    own_resource = FakeOwnedResource(str(uuid4()), worker.worker_id)
    foreign_resource = FakeOwnedResource(str(uuid4()), another_worker.worker_id)

    resolved = authorization.resolve_owned_resource(
        actor=worker,
        resource_kind=authorization.ResourceKind(resource_kind),
        resource_id=own_resource.resource_id,
        lookup=lambda _resource_id: own_resource,
    )
    assert resolved is own_resource

    with pytest.raises(authorization.ResourceNotFound) as foreign_denial:
        authorization.resolve_owned_resource(
            actor=worker,
            resource_kind=authorization.ResourceKind(resource_kind),
            resource_id=foreign_resource.resource_id,
            lookup=lambda _resource_id: foreign_resource,
        )

    with pytest.raises(authorization.ResourceNotFound) as missing_denial:
        authorization.resolve_owned_resource(
            actor=worker,
            resource_kind=authorization.ResourceKind(resource_kind),
            resource_id=str(uuid4()),
            lookup=lambda _resource_id: None,
        )

    assert foreign_denial.value.status_code == 404
    assert foreign_denial.value.response_payload == {"error": {"code": "resource_not_found"}}
    assert foreign_denial.value.response_payload == missing_denial.value.response_payload
    assert str(foreign_denial.value) == str(missing_denial.value)
    assert foreign_resource.resource_id not in str(foreign_denial.value.response_payload)

    foreign_response = foreign_denial.value.to_response()
    missing_response = missing_denial.value.to_response()
    assert foreign_response.status_code == missing_response.status_code == 404
    assert (
        json.loads(foreign_response.content)
        == json.loads(missing_response.content)
        == {"error": {"code": "resource_not_found"}}
    )


@pytest.mark.parametrize("resource_kind", ["assignment", "revision", "media"])
def test_authorization_primitive_audits_admin_sensitive_reads(
    resource_kind: str,
) -> None:
    authorization = authorization_api()
    administrator = create_user(username="review-administrator", role="admin")
    worker = create_user(username="reviewed-worker", role="worker")
    resource = FakeOwnedResource(str(uuid4()), worker.worker_id)

    resolved = authorization.resolve_owned_resource(
        actor=administrator,
        resource_kind=authorization.ResourceKind(resource_kind),
        resource_id=resource.resource_id,
        lookup=lambda _resource_id: resource,
    )

    assert resolved is resource
    audit_event = apps.get_model("identity", "AuditEvent").objects.get(
        actor=administrator,
        target_worker=worker,
        action="resource.sensitive_read",
    )
    assert audit_event.details == {
        "resource_id": resource.resource_id,
        "resource_kind": resource_kind,
    }
