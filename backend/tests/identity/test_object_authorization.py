from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from identity.authorization import ResourceKind, ResourceNotFound, resolve_owned_resource
from identity.models import AuditEvent, User

pytestmark = pytest.mark.django_db


@dataclass(frozen=True)
class FakeOwnedResource:
    resource_id: str
    owner_worker_id: UUID


def create_user(*, username: str, role: str) -> User:
    return User.objects.create_user(
        username=username,
        role=role,
        must_change_password=False,
    )


@pytest.mark.parametrize("resource_kind", ["assignment", "revision"])
def test_authorization_primitive_resolves_only_owned_resources_without_existence_leak(
    resource_kind: str,
) -> None:
    worker = create_user(username="assigned-worker", role="worker")
    another_worker = create_user(username="another-worker", role="worker")
    own_resource = FakeOwnedResource(str(uuid4()), worker.worker_id)
    foreign_resource = FakeOwnedResource(str(uuid4()), another_worker.worker_id)

    resolved = resolve_owned_resource(
        actor=worker,
        resource_kind=ResourceKind(resource_kind),
        resource_id=own_resource.resource_id,
        lookup=lambda _resource_id: own_resource,
    )
    assert resolved is own_resource

    with pytest.raises(ResourceNotFound) as foreign_denial:
        resolve_owned_resource(
            actor=worker,
            resource_kind=ResourceKind(resource_kind),
            resource_id=foreign_resource.resource_id,
            lookup=lambda _resource_id: foreign_resource,
        )

    with pytest.raises(ResourceNotFound) as missing_denial:
        resolve_owned_resource(
            actor=worker,
            resource_kind=ResourceKind(resource_kind),
            resource_id=str(uuid4()),
            lookup=lambda _resource_id: None,
        )

    assert foreign_denial.value.code == missing_denial.value.code == "resource_not_found"
    assert str(foreign_denial.value) == str(missing_denial.value)
    assert foreign_resource.resource_id not in str(foreign_denial.value)


@pytest.mark.parametrize("resource_kind", ["assignment", "revision"])
def test_authorization_primitive_audits_admin_sensitive_reads(
    resource_kind: str,
) -> None:
    administrator = create_user(username="review-administrator", role="admin")
    worker = create_user(username="reviewed-worker", role="worker")
    resource = FakeOwnedResource(str(uuid4()), worker.worker_id)

    resolved = resolve_owned_resource(
        actor=administrator,
        resource_kind=ResourceKind(resource_kind),
        resource_id=resource.resource_id,
        lookup=lambda _resource_id: resource,
    )

    assert resolved is resource
    audit_event = AuditEvent.objects.get(
        actor=administrator,
        target_worker=worker,
        action="resource.sensitive_read",
    )
    assert audit_event.details == {
        "resource_id": resource.resource_id,
        "resource_kind": resource_kind,
    }
