from __future__ import annotations

from math import isfinite

from django.core.exceptions import ValidationError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_POST
from identity.authorization import ResourceNotFound
from identity.http import (
    current_session_key,
    error_response,
    opaque_uuid,
    request_json,
    require_production_worker,
)
from identity.services import WorkspaceLeaseLost
from work.models import OperationalIssue
from work.operations import record_operational_issue

from .models import ActivityEvent
from .services import ACTIVE_TIME_RULE_VERSION, record_activity_event

_EVENT_FIELDS = {
    "active_lease_id",
    "active_time_rule_version",
    "assignment_id",
    "client_build_sha",
    "client_monotonic_ms",
    "client_session_id",
    "client_wall_time_ms",
    "draft_cycle_id",
    "event_id",
    "event_type",
    "focus",
    "interaction_type",
    "sequence_no",
    "tab_id",
    "visibility",
}
_INTERACTIONS = {
    "active_3d_check",
    "annotation_2d_edit",
    "image_zoom_pan",
    "metadata_edit",
    "pair_reorder",
    "undo_redo",
}


def _event_response(event: ActivityEvent) -> dict[str, object]:
    return {
        "event_id": str(event.event_id),
        "server_received_at": event.server_received_at.isoformat().replace("+00:00", "Z"),
    }


@require_POST
def worker_activity_event_view(request: HttpRequest) -> JsonResponse:
    actor = require_production_worker(request)
    if isinstance(actor, JsonResponse):
        return actor
    payload = request_json(request)
    if payload is None or set(payload) != _EVENT_FIELDS:
        return error_response("invalid_activity_event", status=400)
    event_id = opaque_uuid(payload.get("event_id"))
    assignment_id = opaque_uuid(payload.get("assignment_id"))
    draft_cycle_id = opaque_uuid(payload.get("draft_cycle_id"))
    client_session_id = opaque_uuid(payload.get("client_session_id"))
    active_lease_raw = payload.get("active_lease_id")
    active_lease_id = None if active_lease_raw is None else opaque_uuid(active_lease_raw)
    tab_id = opaque_uuid(payload.get("tab_id"))
    sequence_no = payload.get("sequence_no")
    monotonic = payload.get("client_monotonic_ms")
    wall_time = payload.get("client_wall_time_ms")
    event_type = payload.get("event_type")
    visibility = payload.get("visibility")
    focus = payload.get("focus")
    interaction_type = payload.get("interaction_type")
    client_build_sha = payload.get("client_build_sha")
    rule_version = payload.get("active_time_rule_version")
    interaction_allowed = isinstance(interaction_type, str) and interaction_type in _INTERACTIONS
    if (
        event_id is None
        or assignment_id is None
        or draft_cycle_id is None
        or client_session_id is None
        or tab_id is None
        or (active_lease_raw is not None and active_lease_id is None)
        or isinstance(sequence_no, bool)
        or not isinstance(sequence_no, int)
        or sequence_no < 1
        or isinstance(monotonic, bool)
        or not isinstance(monotonic, (int, float))
        or not isfinite(monotonic)
        or monotonic < 0
        or isinstance(wall_time, bool)
        or not isinstance(wall_time, int)
        or wall_time < 0
        or event_type not in ActivityEvent.EventType.values
        or visibility not in ActivityEvent.Visibility.values
        or not isinstance(focus, bool)
        or (event_type == ActivityEvent.EventType.INTERACTION) != interaction_allowed
        or (
            event_type
            in (
                ActivityEvent.EventType.HEARTBEAT,
                ActivityEvent.EventType.IDLE,
                ActivityEvent.EventType.INTERACTION,
            )
            and active_lease_id is None
        )
        or not isinstance(client_build_sha, str)
        or not 1 <= len(client_build_sha) <= 64
        or rule_version != ACTIVE_TIME_RULE_VERSION
    ):
        return error_response("invalid_activity_event", status=400)
    try:
        event, created = record_activity_event(
            actor=actor,
            assignment_id=assignment_id,
            draft_cycle_id=draft_cycle_id,
            event_id=event_id,
            client_session_id=client_session_id,
            active_lease_id=active_lease_id,
            sequence_no=sequence_no,
            event_type=event_type,
            client_monotonic_ms=float(monotonic),
            client_wall_time_ms=wall_time,
            visibility=visibility,
            focus=focus,
            interaction_type=interaction_type,
            client_build_sha=client_build_sha,
            active_time_rule_version=rule_version,
            session_key=current_session_key(request),
            session_token=request.session.get("active_workspace_token"),
            tab_id=tab_id,
        )
    except WorkspaceLeaseLost:
        record_operational_issue(
            actor=actor,
            assignment_id=assignment_id,
            kind=OperationalIssue.Kind.ACTIVITY_EVENT,
            error_code="workspace_lease_lost",
        )
        return error_response("workspace_lease_lost", status=409)
    except ResourceNotFound:
        return error_response(ResourceNotFound.code, status=404)
    except ValidationError as error:
        error_code = error.code or "activity_event_conflict"
        record_operational_issue(
            actor=actor,
            assignment_id=assignment_id,
            kind=OperationalIssue.Kind.ACTIVITY_EVENT,
            error_code=error_code,
        )
        return error_response(error_code, status=409)
    return JsonResponse(_event_response(event), status=201 if created else 200)
