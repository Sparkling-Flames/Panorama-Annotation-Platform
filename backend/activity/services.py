from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from math import isfinite
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from identity.authorization import ResourceNotFound
from identity.models import User
from identity.services import lock_worker_workspace_for_write
from work.models import (
    ACTIVE_TIME_RULE_V1,
    AnnotationRevision,
    Assignment,
    DraftCycle,
    ReworkRequest,
)
from work.services import lock_owned_writable_assignment

from .models import ACTIVITY_MAX_FUTURE_WALL_SKEW_MS, ActivityEvent

IDLE_THRESHOLD_MS = 15_000
ACTIVE_TIME_RULE_VERSION = ACTIVE_TIME_RULE_V1


@transaction.atomic
def record_activity_event(
    *,
    actor: User,
    assignment_id: UUID,
    draft_cycle_id: UUID,
    event_id: UUID,
    client_session_id: UUID,
    active_lease_id: UUID | None,
    sequence_no: int,
    event_type: str,
    client_monotonic_ms: float,
    client_wall_time_ms: int,
    visibility: str,
    focus: bool,
    interaction_type: str | None,
    client_build_sha: str,
    active_time_rule_version: str,
    session_key: str,
    session_token: str | None,
    tab_id: UUID,
) -> tuple[ActivityEvent, bool]:
    lock_worker_workspace_for_write(
        worker=actor,
        session_key=session_key,
        session_token=session_token,
        tab_id=tab_id,
    )
    assignment = lock_owned_writable_assignment(actor=actor, assignment_id=assignment_id)
    if active_time_rule_version != assignment.task.active_time_rule_version:
        raise ValidationError(
            "The activity rule does not match the task.", code="activity_rule_mismatch"
        )
    cycle = DraftCycle.objects.filter(cycle_id=draft_cycle_id, assignment=assignment).first()
    if cycle is None:
        raise ResourceNotFound
    values = {
        "worker_id": actor.pk,
        "assignment_id": assignment.assignment_id,
        "draft_cycle_id": cycle.cycle_id,
        "client_session_id": client_session_id,
        "active_lease_id": active_lease_id,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "client_monotonic_ms": client_monotonic_ms,
        "client_wall_time_ms": client_wall_time_ms,
        "visibility": visibility,
        "focus": focus,
        "interaction_type": interaction_type,
        "client_build_sha": client_build_sha,
        "active_time_rule_version": active_time_rule_version,
    }
    existing = ActivityEvent.objects.filter(event_id=event_id).first()
    if existing is not None:
        if any(getattr(existing, field) != value for field, value in values.items()):
            raise ValidationError(
                "The activity event ID was used for another event.",
                code="activity_event_conflict",
            )
        return existing, False
    if ActivityEvent.objects.filter(
        worker=actor,
        client_session_id=client_session_id,
        sequence_no=sequence_no,
    ).exists():
        raise ValidationError(
            "The activity sequence number is already used.",
            code="activity_sequence_conflict",
        )
    return ActivityEvent.objects.create(event_id=event_id, **values), True


def _derive_stream(events: Iterable[ActivityEvent]) -> tuple[list[tuple[float, float]], set[str]]:
    intervals: list[tuple[float, float]] = []
    reasons: set[str] = set()
    accounted_until: float | None = None
    accounted_wall: float | None = None
    active_until: float | None = None
    active_lease_id: UUID | None = None
    last_wall: int | None = None
    for event in sorted(events, key=lambda item: item.sequence_no):
        current = event.client_monotonic_ms
        if not isfinite(current) or current < 0:
            reasons.add("clock_invalid")
            accounted_until = accounted_wall = active_until = active_lease_id = None
            continue
        wall = event.client_wall_time_ms
        server_wall = int(event.server_received_at.timestamp() * 1_000)
        if wall < 0 or (last_wall is not None and wall < last_wall):
            reasons.add("wall_clock_non_monotonic")
            accounted_until = accounted_wall = active_until = active_lease_id = None
            continue
        if wall > server_wall + ACTIVITY_MAX_FUTURE_WALL_SKEW_MS:
            reasons.add("wall_clock_future")
            accounted_until = accounted_wall = active_until = active_lease_id = None
            continue
        last_wall = wall
        if accounted_until is not None and active_until is not None:
            if current < accounted_until:
                reasons.add("clock_non_monotonic")
                accounted_until = accounted_wall = active_until = active_lease_id = None
                continue
            end = min(current, active_until)
            if end > accounted_until:
                if accounted_wall is None:
                    raise RuntimeError("Activity interval is missing its wall-clock anchor.")
                next_wall = accounted_wall + end - accounted_until
                intervals.append((accounted_wall, next_wall))
                accounted_wall = next_wall
                accounted_until = end
            if current > active_until:
                reasons.add("interval_capped")
        if (
            event.event_type == ActivityEvent.EventType.INTERACTION
            and event.focus
            and event.visibility == ActivityEvent.Visibility.VISIBLE
        ):
            if active_lease_id is not None and event.active_lease_id != active_lease_id:
                reasons.add("lease_switched")
            active_lease_id = event.active_lease_id
            accounted_until = current
            accounted_wall = float(wall)
            active_until = current + IDLE_THRESHOLD_MS
        elif event.active_lease_id != active_lease_id:
            continue
        elif (
            event.event_type == ActivityEvent.EventType.IDLE
            or not event.focus
            or event.visibility == ActivityEvent.Visibility.HIDDEN
        ):
            active_lease_id = None
            accounted_until = None
            accounted_wall = None
            active_until = None
    return intervals, reasons


def _union_milliseconds(intervals: Iterable[tuple[float, float]]) -> int:
    ordered = sorted(intervals)
    if not ordered:
        return 0
    start, end = ordered[0]
    total = 0.0
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return int(round(total + end - start))


def derive_assignment_activity(*, actor: User, assignment_id: UUID) -> dict[str, object]:
    assignment = (
        Assignment.objects.select_related("task")
        .filter(assignment_id=assignment_id, worker=actor)
        .first()
    )
    if assignment is None:
        raise ResourceNotFound
    rule_version = assignment.task.active_time_rule_version
    if rule_version != ACTIVE_TIME_RULE_VERSION:
        raise ValidationError("Unsupported activity rule.", code="activity_rule_unsupported")
    events = list(ActivityEvent.objects.filter(assignment=assignment).select_related("draft_cycle"))
    streams: dict[tuple[UUID, UUID], list[ActivityEvent]] = defaultdict(list)
    for event in events:
        streams[(event.draft_cycle_id, event.client_session_id)].append(event)
    cycle_intervals: dict[UUID, list[tuple[float, float]]] = defaultdict(list)
    reasons: set[str] = set()
    for (cycle_id, _client_session_id), stream in streams.items():
        if any(event.active_time_rule_version != rule_version for event in stream):
            reasons.add("rule_version_mismatch")
            continue
        intervals, stream_reasons = _derive_stream(stream)
        cycle_intervals[cycle_id].extend(intervals)
        reasons.update(stream_reasons)
    revision_cycles = set(
        AnnotationRevision.objects.filter(assignment=assignment).values_list(
            "draft_cycle_id", flat=True
        )
    )
    rework_cycles = set(
        ReworkRequest.objects.filter(assignment=assignment, draft_cycle__isnull=False).values_list(
            "draft_cycle_id", flat=True
        )
    )
    buckets = {"initial_ms": 0, "revision_ms": 0, "rework_ms": 0, "unsubmitted_ms": 0}
    for cycle in DraftCycle.objects.filter(assignment=assignment):
        milliseconds = _union_milliseconds(cycle_intervals[cycle.cycle_id])
        if cycle.cycle_id in rework_cycles:
            buckets["rework_ms"] += milliseconds
        elif cycle.cycle_id not in revision_cycles:
            buckets["unsubmitted_ms"] += milliseconds
        elif cycle.cycle_no == 1:
            buckets["initial_ms"] += milliseconds
        else:
            buckets["revision_ms"] += milliseconds
    return {
        "active_time_rule_version": rule_version,
        **buckets,
        "reasons": sorted(reasons),
        "total_ms": sum(buckets.values()),
    }
