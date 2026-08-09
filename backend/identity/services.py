from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from .models import ActiveWorkspace, AuditEvent, User


class WorkspaceConflict(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class WorkspaceLeaseLost(Exception):
    pass


@dataclass(frozen=True)
class WorkspaceAcquisition:
    workspace: ActiveWorkspace
    rotated: bool


def temporary_password() -> str:
    return secrets.token_urlsafe(18)


def record_account_audit(
    *,
    actor: User,
    target_worker: User,
    action: str,
    details: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor,
        target_worker=target_worker,
        action=action,
        details=details or {},
    )


def revoke_user_sessions(user: User) -> int:
    session_keys: list[str] = []
    for session in Session.objects.all().iterator():
        session_user_id = session.get_decoded().get("_auth_user_id")
        if session_user_id == str(user.pk):
            session_keys.append(session.session_key)

    if not session_keys:
        return 0
    deleted_count, _ = Session.objects.filter(session_key__in=session_keys).delete()
    return deleted_count


@transaction.atomic
def create_worker_account(*, actor: User, username: str) -> tuple[User, str]:
    issued_password = temporary_password()
    worker = User(username=username, role=User.Role.WORKER, must_change_password=True)
    worker.set_password(issued_password)
    worker.full_clean()
    worker.save()
    record_account_audit(
        actor=actor,
        target_worker=worker,
        action="worker.created",
        details={"username": worker.username},
    )
    return worker, issued_password


@transaction.atomic
def reset_worker_password(*, actor: User, worker: User) -> str:
    issued_password = temporary_password()
    worker.set_password(issued_password)
    worker.must_change_password = True
    worker.save(update_fields=["password", "must_change_password"])
    revoked_count = revoke_user_sessions(worker)
    record_account_audit(
        actor=actor,
        target_worker=worker,
        action="worker.password_reset",
        details={"revoked_session_count": revoked_count},
    )
    return issued_password


@transaction.atomic
def revoke_worker_sessions(*, actor: User, worker: User) -> None:
    revoked_count = revoke_user_sessions(worker)
    record_account_audit(
        actor=actor,
        target_worker=worker,
        action="worker.sessions_revoked",
        details={"revoked_session_count": revoked_count},
    )


@transaction.atomic
def set_worker_enabled(*, actor: User, worker: User, enabled: bool) -> None:
    worker.is_active = enabled
    worker.save(update_fields=["is_active"])
    revoked_count = revoke_user_sessions(worker)
    record_account_audit(
        actor=actor,
        target_worker=worker,
        action="worker.restored" if enabled else "worker.disabled",
        details={"revoked_session_count": revoked_count},
    )


@transaction.atomic
def acquire_worker_workspace(
    *,
    worker: User,
    session_key: str,
    client_instance_id: UUID,
    tab_id: UUID,
    takeover: bool,
) -> WorkspaceAcquisition:
    locked_worker = User.objects.select_for_update().get(pk=worker.pk)
    workspace = ActiveWorkspace.objects.select_for_update().filter(worker=locked_worker).first()
    now = timezone.now()
    expires_at = now + timedelta(seconds=settings.WORKSPACE_LEASE_SECONDS)

    if workspace is None:
        workspace = ActiveWorkspace.objects.create(
            worker=locked_worker,
            session_key=session_key,
            client_instance_id=client_instance_id,
            tab_id=tab_id,
            lease_expires_at=expires_at,
        )
        record_account_audit(
            actor=locked_worker,
            target_worker=locked_worker,
            action="workspace.acquired",
            details={"reason": "initial"},
        )
        return WorkspaceAcquisition(workspace=workspace, rotated=True)

    if workspace.lease_expires_at <= now:
        acquisition_reason = "expired"
    elif workspace.session_key == session_key:
        if workspace.client_instance_id != client_instance_id or workspace.tab_id != tab_id:
            raise WorkspaceConflict("workspace_tab_conflict")
        workspace.lease_expires_at = expires_at
        workspace.save(update_fields=["lease_expires_at", "updated_at"])
        return WorkspaceAcquisition(workspace=workspace, rotated=False)
    elif not takeover:
        raise WorkspaceConflict("workspace_takeover_required")
    else:
        acquisition_reason = "takeover"

    workspace.token = uuid4()
    workspace.session_key = session_key
    workspace.client_instance_id = client_instance_id
    workspace.tab_id = tab_id
    workspace.lease_expires_at = expires_at
    workspace.save(
        update_fields=[
            "token",
            "session_key",
            "client_instance_id",
            "tab_id",
            "lease_expires_at",
            "updated_at",
        ]
    )
    record_account_audit(
        actor=locked_worker,
        target_worker=locked_worker,
        action="workspace.taken_over" if acquisition_reason == "takeover" else "workspace.acquired",
        details={"reason": acquisition_reason},
    )
    return WorkspaceAcquisition(workspace=workspace, rotated=True)


@transaction.atomic
def renew_worker_workspace(
    *,
    worker: User,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> None:
    workspace = lock_worker_workspace_for_write(
        worker=worker,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    workspace.lease_expires_at = timezone.now() + timedelta(
        seconds=settings.WORKSPACE_LEASE_SECONDS
    )
    workspace.save(update_fields=["lease_expires_at", "updated_at"])


def lock_worker_workspace_for_write(
    *,
    worker: User,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> ActiveWorkspace:
    locked_worker = User.objects.select_for_update().get(pk=worker.pk)
    workspace = ActiveWorkspace.objects.select_for_update().filter(worker=locked_worker).first()
    try:
        parsed_token = UUID(session_token) if session_token is not None else None
    except (TypeError, ValueError) as error:
        raise WorkspaceLeaseLost from error

    now = timezone.now()
    if (
        workspace is None
        or workspace.lease_expires_at <= now
        or workspace.session_key != session_key
        or workspace.token != parsed_token
        or workspace.tab_id != tab_id
    ):
        raise WorkspaceLeaseLost
    return workspace
