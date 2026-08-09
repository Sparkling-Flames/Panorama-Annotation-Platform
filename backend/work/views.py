from __future__ import annotations

from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from identity.authorization import ResourceNotFound
from identity.models import User
from identity.services import WorkspaceLeaseLost, record_account_audit
from identity.views import current_session_key, opaque_uuid, request_json, request_user

from .models import Assignment, Task, WorkBatch
from .services import (
    assign_task,
    create_work_batch,
    get_owned_assignment,
    open_owned_assignment,
    set_owned_assignment_queue_state,
)


def error_response(code: str, *, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code}}, status=status)


def require_worker(request: HttpRequest) -> tuple[User | None, JsonResponse | None]:
    actor = request_user(request)
    if actor is None:
        return None, error_response("authentication_required", status=401)
    if actor.role != User.Role.WORKER:
        return None, error_response("worker_required", status=403)
    if actor.must_change_password:
        return None, error_response("password_change_required", status=403)
    return actor, None


def require_admin(request: HttpRequest) -> tuple[User | None, JsonResponse | None]:
    actor = request_user(request)
    if actor is None:
        return None, error_response("authentication_required", status=401)
    if actor.role != User.Role.ADMIN:
        return None, error_response("admin_required", status=403)
    return actor, None


def assignment_payload(assignment: Assignment) -> dict[str, object]:
    return {
        "assignment_id": str(assignment.assignment_id),
        "batch_id": str(assignment.batch_id),
        "queue_state": assignment.queue_state,
        "review_state": assignment.review_state,
        "task": {
            "external_task_key": assignment.task.external_task_key,
            "mode": assignment.task.mode,
            "task_id": str(assignment.task_id),
        },
        "work_state": assignment.work_state,
    }


@require_POST
def admin_work_batches_view(request: HttpRequest) -> JsonResponse:
    _actor, denied = require_admin(request)
    if denied is not None:
        return denied
    payload = request_json(request)
    if payload is None or set(payload) != {"name"} or not isinstance(payload.get("name"), str):
        return error_response("invalid_work_batch", status=400)
    try:
        batch = create_work_batch(name=payload["name"])
    except ValidationError as error:
        return error_response(error.code or "invalid_work_batch", status=400)
    return JsonResponse(
        {"batch_id": str(batch.batch_id), "name": batch.name, "status": batch.status},
        status=201,
    )


@require_POST
def admin_batch_assignments_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor, denied = require_admin(request)
    if denied is not None:
        return denied
    payload = request_json(request)
    if payload is None or set(payload) != {"task_id", "worker_id"}:
        return error_response("invalid_assignment", status=400)
    task_id = opaque_uuid(payload.get("task_id"))
    worker_id = opaque_uuid(payload.get("worker_id"))
    if task_id is None or worker_id is None or actor is None:
        return error_response("invalid_assignment", status=400)
    batch = WorkBatch.objects.filter(batch_id=batch_id, status=WorkBatch.Status.OPEN).first()
    task = Task.objects.filter(
        task_id=task_id,
        status=Task.Status.PUBLISHED,
    ).first()
    worker = User.objects.filter(
        worker_id=worker_id,
        role=User.Role.WORKER,
        is_active=True,
    ).first()
    if batch is None or task is None or worker is None:
        return error_response("resource_not_found", status=404)
    try:
        assignment = assign_task(batch=batch, task=task, worker=worker)
    except (IntegrityError, ValidationError):
        return error_response("assignment_conflict", status=409)
    record_account_audit(
        actor=actor,
        target_worker=worker,
        action="assignment.created",
        details={
            "assignment_id": str(assignment.assignment_id),
            "batch_id": str(batch.batch_id),
            "task_id": str(task.task_id),
        },
    )
    return JsonResponse(assignment_payload(assignment), status=201)


@require_GET
def worker_batches_view(request: HttpRequest) -> JsonResponse:
    actor, denied = require_worker(request)
    if denied is not None:
        return denied
    batches = WorkBatch.objects.filter(assignments__worker=actor).distinct()
    return JsonResponse(
        {
            "batches": [
                {"batch_id": str(batch.batch_id), "name": batch.name, "status": batch.status}
                for batch in batches
            ]
        }
    )


@require_GET
def worker_batch_assignments_view(request: HttpRequest, batch_id: UUID) -> JsonResponse:
    actor, denied = require_worker(request)
    if denied is not None:
        return denied
    assignments = list(
        Assignment.objects.select_related("batch", "task")
        .filter(batch_id=batch_id, worker=actor)
        .order_by("created_at", "assignment_id")
    )
    if not assignments:
        return error_response("resource_not_found", status=404)
    return JsonResponse({"assignments": [assignment_payload(item) for item in assignments]})


@require_GET
def worker_assignment_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor, denied = require_worker(request)
    if denied is not None:
        return denied
    if actor is None:
        return error_response("authentication_required", status=401)
    try:
        assignment = get_owned_assignment(actor=actor, assignment_id=assignment_id)
    except ResourceNotFound as error:
        return error.to_response()
    return JsonResponse(assignment_payload(assignment))


def _write_payload(request: HttpRequest) -> tuple[dict[str, object] | None, UUID | None]:
    payload = request_json(request)
    if payload is None:
        return None, None
    return payload, opaque_uuid(payload.get("tab_id"))


@require_POST
def worker_assignment_open_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor, denied = require_worker(request)
    if denied is not None:
        return denied
    payload, tab_id = _write_payload(request)
    if payload is None or set(payload) != {"tab_id"} or tab_id is None:
        return error_response("invalid_assignment_open", status=400)
    if actor is None:
        return error_response("authentication_required", status=401)
    try:
        assignment = open_owned_assignment(
            actor=actor,
            assignment_id=assignment_id,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound as error:
        return error.to_response()
    except ValidationError as error:
        return error_response(error.code or "assignment_not_editable", status=409)
    return JsonResponse(assignment_payload(assignment))


@require_POST
def worker_assignment_queue_state_view(request: HttpRequest, assignment_id: UUID) -> JsonResponse:
    actor, denied = require_worker(request)
    if denied is not None:
        return denied
    payload, tab_id = _write_payload(request)
    if payload is None or set(payload) != {"queue_state", "tab_id"} or tab_id is None:
        return error_response("invalid_queue_state_request", status=400)
    if actor is None:
        return error_response("authentication_required", status=401)
    queue_state = payload.get("queue_state")
    if not isinstance(queue_state, str):
        return error_response("queue_state_invalid", status=400)
    try:
        assignment = set_owned_assignment_queue_state(
            actor=actor,
            assignment_id=assignment_id,
            queue_state=queue_state,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound as error:
        return error.to_response()
    except ValidationError as error:
        status = 400 if error.code == "queue_state_invalid" else 409
        return error_response(error.code or "queue_state_invalid", status=status)
    return JsonResponse(assignment_payload(assignment))
