from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse

from .models import User
from .services import has_current_data_notice


def error_response(code: str, *, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code}}, status=status)


def request_json(request: HttpRequest) -> dict[str, Any] | None:
    try:
        value = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def request_user(request: HttpRequest) -> User | None:
    candidate = getattr(request, "user", None)
    return candidate if isinstance(candidate, User) and candidate.is_authenticated else None


def require_admin(request: HttpRequest) -> User | JsonResponse:
    user = request_user(request)
    if user is None:
        return error_response("authentication_required", status=401)
    if user.role != User.Role.ADMIN:
        return error_response("admin_required", status=403)
    return user


def require_worker(request: HttpRequest) -> User | JsonResponse:
    user = request_user(request)
    if user is None:
        return error_response("authentication_required", status=401)
    if user.role != User.Role.WORKER:
        return error_response("worker_required", status=403)
    if user.must_change_password:
        return error_response("password_change_required", status=403)
    return user


def require_production_worker(request: HttpRequest) -> User | JsonResponse:
    """Require a worker who may enter the production workspace.

    Notice retrieval and acceptance deliberately use ``require_worker`` so a
    worker can satisfy the prerequisite without already having access.
    """
    user = require_worker(request)
    if isinstance(user, JsonResponse):
        return user
    if not has_current_data_notice(user):
        return error_response("notice_acceptance_required", status=409)
    return user


def opaque_uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def current_session_key(request: HttpRequest) -> str:
    if request.session.session_key is None:
        request.session.create()
    session_key = request.session.session_key
    if session_key is None:
        raise RuntimeError("Django session backend did not allocate a session key")
    return session_key
