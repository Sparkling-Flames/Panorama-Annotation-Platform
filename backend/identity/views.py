from __future__ import annotations

from django.contrib.auth import authenticate, login, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .http import (
    current_session_key,
    error_response,
    opaque_uuid,
    request_json,
    request_user,
    require_admin,
)
from .models import User
from .services import (
    WorkspaceConflict,
    WorkspaceLeaseLost,
    acquire_worker_workspace,
    create_worker_account,
    record_account_audit,
    renew_worker_workspace,
    reset_worker_password,
    revoke_worker_sessions,
    set_worker_enabled,
)


def worker_payload(worker: User) -> dict[str, object]:
    return {
        "worker_id": str(worker.worker_id),
        "username": worker.username,
        "role": worker.role,
        "must_change_password": worker.must_change_password,
        "is_active": worker.is_active,
    }


def find_worker(worker_id: str) -> User | None:
    try:
        return User.objects.get(worker_id=worker_id, role=User.Role.WORKER)
    except (User.DoesNotExist, ValidationError, ValueError):
        return None


@require_GET
@ensure_csrf_cookie
def csrf_view(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"csrf_ready": True})


@require_GET
def session_view(request: HttpRequest) -> JsonResponse:
    user = request_user(request)
    if user is None:
        return JsonResponse({"authenticated": False})
    return JsonResponse(
        {
            "authenticated": True,
            "must_change_password": user.must_change_password,
            "role": user.role,
        }
    )


@require_POST
def login_view(request: HttpRequest) -> JsonResponse:
    payload = request_json(request)
    if payload is None:
        return error_response("invalid_json", status=400)
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return error_response("invalid_credentials", status=401)

    user = authenticate(request, username=username, password=password)
    if not isinstance(user, User):
        return error_response("invalid_credentials", status=401)
    login(request, user)
    return JsonResponse(
        {
            "must_change_password": user.must_change_password,
            "workspace_access": user.role == User.Role.WORKER and not user.must_change_password,
        }
    )


@require_POST
def change_password_view(request: HttpRequest) -> HttpResponse:
    user = request_user(request)
    if user is None:
        return error_response("authentication_required", status=401)
    payload = request_json(request)
    if payload is None:
        return error_response("invalid_json", status=400)
    current_password = payload.get("current_password")
    new_password = payload.get("new_password")
    if not isinstance(current_password, str) or not user.check_password(current_password):
        return error_response("invalid_current_password", status=400)
    if not isinstance(new_password, str):
        return error_response("invalid_new_password", status=400)
    try:
        validate_password(new_password, user=user)
    except ValidationError:
        return error_response("invalid_new_password", status=400)

    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    update_session_auth_hash(request, user)
    record_account_audit(
        actor=user,
        target_worker=user,
        action="worker.password_changed",
    )
    return HttpResponse(status=204)


@require_GET
def workspace_session_view(request: HttpRequest) -> JsonResponse:
    user = request_user(request)
    if user is None:
        return error_response("authentication_required", status=401)
    if user.must_change_password:
        return error_response("password_change_required", status=403)
    return JsonResponse({"workspace_access": True})


@require_http_methods(["POST"])
def workers_collection_view(request: HttpRequest) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if payload is None:
        return error_response("invalid_json", status=400)
    if "worker_id" in payload:
        return error_response("server_assigned_field", status=400)
    if set(payload) != {"username"}:
        return error_response("invalid_account_fields", status=400)
    username = payload.get("username")
    if not isinstance(username, str) or not username.strip():
        return error_response("invalid_username", status=400)

    try:
        worker, issued_password = create_worker_account(actor=actor, username=username.strip())
    except ValidationError:
        return error_response("invalid_username", status=400)
    except IntegrityError:
        return error_response("username_conflict", status=409)

    response_payload = worker_payload(worker)
    response_payload["temporary_password"] = issued_password
    return JsonResponse(response_payload, status=201)


@require_GET
def worker_detail_view(request: HttpRequest, worker_id: str) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    worker = find_worker(worker_id)
    if worker is None:
        return error_response("worker_not_found", status=404)
    return JsonResponse(worker_payload(worker))


@require_POST
def reset_password_view(request: HttpRequest, worker_id: str) -> JsonResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    worker = find_worker(worker_id)
    if worker is None:
        return error_response("worker_not_found", status=404)
    issued_password = reset_worker_password(actor=actor, worker=worker)
    return JsonResponse(
        {
            "must_change_password": worker.must_change_password,
            "temporary_password": issued_password,
        }
    )


@require_POST
def revoke_sessions_view(request: HttpRequest, worker_id: str) -> HttpResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    worker = find_worker(worker_id)
    if worker is None:
        return error_response("worker_not_found", status=404)
    revoke_worker_sessions(actor=actor, worker=worker)
    return HttpResponse(status=204)


def set_enabled_view(request: HttpRequest, worker_id: str, *, enabled: bool) -> HttpResponse:
    actor = require_admin(request)
    if isinstance(actor, JsonResponse):
        return actor
    worker = find_worker(worker_id)
    if worker is None:
        return error_response("worker_not_found", status=404)
    set_worker_enabled(actor=actor, worker=worker, enabled=enabled)
    return HttpResponse(status=204)


@require_POST
def disable_worker_view(request: HttpRequest, worker_id: str) -> HttpResponse:
    return set_enabled_view(request, worker_id, enabled=False)


@require_POST
def restore_worker_view(request: HttpRequest, worker_id: str) -> HttpResponse:
    return set_enabled_view(request, worker_id, enabled=True)


@require_POST
def acquire_workspace_view(request: HttpRequest) -> JsonResponse:
    user = request_user(request)
    if user is None:
        return error_response("authentication_required", status=401)
    if user.role == User.Role.ADMIN:
        return JsonResponse({"workspace_required": False})
    if user.must_change_password:
        return error_response("password_change_required", status=403)

    payload = request_json(request)
    if payload is None:
        return error_response("invalid_json", status=400)
    client_instance_id = opaque_uuid(payload.get("client_instance_id"))
    tab_id = opaque_uuid(payload.get("tab_id"))
    takeover = payload.get("takeover", False)
    if client_instance_id is None or tab_id is None or not isinstance(takeover, bool):
        return error_response("invalid_workspace_request", status=400)

    try:
        acquisition = acquire_worker_workspace(
            worker=user,
            session_key=current_session_key(request),
            client_instance_id=client_instance_id,
            tab_id=tab_id,
            takeover=takeover,
        )
    except WorkspaceConflict as conflict:
        return error_response(conflict.code, status=409)

    request.session["active_workspace_token"] = str(acquisition.workspace.token)
    return JsonResponse(
        {
            "lease_expires_at": acquisition.workspace.lease_expires_at.isoformat(),
            "workspace_state": "editable",
        },
        status=201 if acquisition.rotated else 200,
    )


@require_POST
def renew_workspace_view(request: HttpRequest) -> HttpResponse:
    user = request_user(request)
    if user is None:
        return error_response("authentication_required", status=401)
    if user.role == User.Role.ADMIN:
        return HttpResponse(status=204)
    payload = request_json(request)
    if payload is None:
        return error_response("invalid_json", status=400)
    tab_id = opaque_uuid(payload.get("tab_id"))
    if tab_id is None:
        return error_response("invalid_workspace_request", status=400)

    try:
        renew_worker_workspace(
            worker=user,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    return HttpResponse(status=204)
