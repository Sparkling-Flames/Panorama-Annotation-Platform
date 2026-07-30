from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol, TypeVar
from uuid import UUID

from django.http import JsonResponse

from .models import User
from .services import record_account_audit


class ResourceKind(StrEnum):
    ASSIGNMENT = "assignment"
    REVISION = "revision"
    MEDIA = "media"


class OwnedResource(Protocol):
    resource_id: str
    owner_worker_id: UUID


ResourceT = TypeVar("ResourceT", bound=OwnedResource)


class ResourceNotFound(Exception):
    status_code = 404

    def __init__(self) -> None:
        super().__init__("resource_not_found")

    @property
    def response_payload(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": "resource_not_found"}}

    def to_response(self) -> JsonResponse:
        return JsonResponse(self.response_payload, status=self.status_code)


def resolve_owned_resource(
    *,
    actor: User,
    resource_kind: ResourceKind,
    resource_id: str,
    lookup: Callable[[str], ResourceT | None],
) -> ResourceT:
    resource = lookup(resource_id)
    if resource is None or not actor.is_active:
        raise ResourceNotFound

    if actor.role == User.Role.WORKER:
        if actor.worker_id != resource.owner_worker_id:
            raise ResourceNotFound
        return resource

    if actor.role != User.Role.ADMIN:
        raise ResourceNotFound

    target_worker = User.objects.filter(
        worker_id=resource.owner_worker_id,
        role=User.Role.WORKER,
    ).first()
    if target_worker is None:
        raise ResourceNotFound

    record_account_audit(
        actor=actor,
        target_worker=target_worker,
        action="resource.sensitive_read",
        details={
            "resource_id": resource.resource_id,
            "resource_kind": resource_kind.value,
        },
    )
    return resource
