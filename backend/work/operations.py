from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import TypedDict, cast
from uuid import UUID

from activity.models import ActivityEvent
from activity.services import derive_assignment_activity
from django.db import DatabaseError
from django.utils import timezone
from identity.authorization import ResourceNotFound
from identity.models import ActiveWorkspace, User

from .models import (
    AnnotationRevision,
    Assignment,
    BlockReport,
    OperationalIssue,
    SubmissionAssessment,
    TaskAggregate,
    WorkBatch,
)

logger = logging.getLogger(__name__)


class _WorkerStatistics(TypedDict):
    accepted: int
    active_time_ms_by_rule: defaultdict[str, int]
    block_reports: int
    deferred: int
    rework_requests: int
    scope_observations: defaultdict[str, int]
    submitted: int
    worker_id: str


def _worker_statistics(batch: WorkBatch) -> list[_WorkerStatistics]:
    by_worker: dict[UUID, _WorkerStatistics] = {}
    # ponytail: derive per Assignment for the POC; batch derivation can replace this if profiling
    # shows the admin refresh path is hot.
    assignments = Assignment.objects.filter(batch=batch).select_related("worker")
    for assignment in assignments:
        worker_id = assignment.worker.worker_id
        statistics = by_worker.setdefault(
            worker_id,
            {
                "accepted": 0,
                "active_time_ms_by_rule": defaultdict(int),
                "block_reports": 0,
                "deferred": 0,
                "rework_requests": 0,
                "scope_observations": defaultdict(int),
                "submitted": 0,
                "worker_id": str(worker_id),
            },
        )
        statistics["accepted"] += assignment.review_state == Assignment.ReviewState.ACCEPTED
        statistics["block_reports"] += assignment.block_reports.count()
        statistics["deferred"] += assignment.queue_state == Assignment.QueueState.DEFERRED
        statistics["rework_requests"] += assignment.rework_requests.count()
        statistics["submitted"] += assignment.work_state == Assignment.WorkState.SUBMITTED

        latest_revision = assignment.revisions.order_by("-revision_no").first()
        if latest_revision is not None:
            observation = latest_revision.state.get("worker_scope_observation")
            if isinstance(observation, str):
                statistics["scope_observations"][observation] += 1

        activity = derive_assignment_activity(
            actor=assignment.worker,
            assignment_id=assignment.assignment_id,
        )
        statistics["active_time_ms_by_rule"][cast(str, activity["active_time_rule_version"])] += (
            cast(int, activity["total_ms"])
        )

    return sorted(by_worker.values(), key=lambda item: item["worker_id"])


def record_operational_issue(
    *,
    actor: User,
    assignment_id: UUID,
    kind: str,
    error_code: str,
) -> OperationalIssue | None:
    assignment = Assignment.objects.filter(assignment_id=assignment_id, worker=actor).first()
    if assignment is None:
        return None
    try:
        return OperationalIssue.objects.create(
            assignment=assignment,
            kind=kind,
            error_code=error_code,
        )
    except DatabaseError:
        logger.warning(
            "Unable to record operational issue kind=%s code=%s",
            kind,
            error_code,
        )
        return None


def batch_operational_snapshot(*, batch_id: UUID) -> dict[str, object]:
    batch = WorkBatch.objects.filter(batch_id=batch_id).first()
    if batch is None:
        raise ResourceNotFound

    submitted_tasks = (
        Assignment.objects.filter(
            batch=batch,
            work_state=Assignment.WorkState.SUBMITTED,
        )
        .values("task_id")
        .distinct()
    )
    submitted = submitted_tasks.count()
    aggregates = TaskAggregate.objects.filter(batch=batch, task_id__in=submitted_tasks)
    resolved = aggregates.filter(terminal_state=TaskAggregate.TerminalState.RESOLVED).count()
    unresolved = aggregates.filter(terminal_state=TaskAggregate.TerminalState.UNRESOLVED).count()
    issues = OperationalIssue.objects.filter(assignment__batch=batch)
    revisions = AnnotationRevision.objects.filter(assignment__batch=batch)
    # ponytail: one batch scan is enough for the POC; store a derived metric if volume proves it hot.
    event_delay = max(
        (
            (received - datetime.fromtimestamp(wall_time / 1000, tz=UTC)).total_seconds() * 1000
            for received, wall_time in ActivityEvent.objects.filter(
                assignment__batch=batch
            ).values_list("server_received_at", "client_wall_time_ms")
        ),
        default=0,
    )

    return {
        "batch_id": str(batch.batch_id),
        "counts": {
            "active_workers": ActiveWorkspace.objects.filter(
                lease_expires_at__gt=timezone.now(),
                worker__assignments__batch=batch,
            )
            .distinct()
            .count(),
            "block_reports": BlockReport.objects.filter(assignment__batch=batch).count(),
            "deferred": Assignment.objects.filter(
                batch=batch,
                queue_state=Assignment.QueueState.DEFERRED,
            ).count(),
            "event_errors": issues.filter(kind=OperationalIssue.Kind.ACTIVITY_EVENT).count(),
            "media_errors": issues.filter(kind=OperationalIssue.Kind.MEDIA_DELIVERY).count(),
            "pending": submitted - resolved - unresolved,
            "resolved": resolved,
            "revisions": revisions.count(),
            "save_errors": issues.filter(kind=OperationalIssue.Kind.DRAFT_SAVE).count(),
            "structure_blocks": SubmissionAssessment.objects.filter(
                revision__assignment__batch=batch,
                structure_valid=False,
            ).count()
            + issues.filter(kind=OperationalIssue.Kind.ANNOTATION_STRUCTURE).count(),
            "submitted": submitted,
            "unresolved": unresolved,
            "verification_pending": revisions.filter(submission_assessments__isnull=True).count(),
        },
        "max_event_delay_ms": max(0, round(event_delay or 0)),
        "refreshed_at": timezone.now().isoformat().replace("+00:00", "Z"),
        "workers": _worker_statistics(batch),
    }
