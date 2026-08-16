from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from math import isfinite
from typing import cast
from uuid import UUID

from activity.models import ACTIVITY_MAX_FUTURE_WALL_SKEW_MS, ActivityEvent
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from identity.authorization import ResourceNotFound

from .annotation_state import (
    AnnotationStateError,
    annotation_state_sha,
    validate_annotation_submission,
)
from .consensus_aggregation import CanonicalComponentAggregation, aggregate_canonical_components
from .manifests import json_sha256
from .models import (
    AnalysisJob,
    AnnotationRevision,
    Assignment,
    AssignmentProposal,
    AuditArtifact,
    SubmissionAssessment,
    Task,
    TaskAggregate,
    TaskConsensusArtifact,
    TaskEligibilityArtifact,
    WorkBatch,
)
from .scope_aggregation import (
    CONSENSUS_POLICY_V2,
    aggregate_scope_evidence,
    consensus_addition_count,
    validate_consensus_policy,
    validate_scope_policy,
)

SUBMISSION_ASSESSMENT_KIND = "submission_assessment"
SUBMISSION_ASSESSMENT_RULE_V1 = "submission-assessment-v1"
SUBMISSION_ASSESSMENT_CODE_V1 = "submission-assessment-code-v1"
AUDIT_RULE_V1 = "audit-rule-v1"
AUDIT_CODE_V1 = "audit-code-v1"
REGISTERED_AUDIT_TYPES = (
    "canonical_structure",
    "scope_portal",
    "time_integrity",
)
JOB_RETRY_DELAY = timedelta(seconds=30)
JOB_LEASE = timedelta(minutes=5)


def submission_assessment_input_sha(revision: AnnotationRevision) -> str:
    return json_sha256(
        {
            "copy_version": revision.meta_copy_version,
            "revision_id": str(revision.revision_id),
            "rule_version": SUBMISSION_ASSESSMENT_RULE_V1,
            "schema_version": revision.meta_schema_version,
            "state_sha": revision.state_sha,
            "task_id": str(revision.task_id),
        }
    )


def eligible_task_input_manifest(*, task_id: UUID, batch_id: UUID) -> dict[str, object]:
    latest_by_worker: dict[object, AnnotationRevision] = {}
    for revision in AnnotationRevision.objects.filter(
        task_id=task_id,
        assignment__batch_id=batch_id,
        completed_rework_request__isnull=True,
        draft_cycle__guidance_events__isnull=True,
    ).order_by("worker_id", "-revision_no"):
        latest_by_worker.setdefault(revision.worker_id, revision)
    assessments = {
        assessment.revision_id: assessment
        for assessment in SubmissionAssessment.objects.filter(
            revision_id__in=(revision.revision_id for revision in latest_by_worker.values()),
            rule_version=SUBMISSION_ASSESSMENT_RULE_V1,
            structure_valid=True,
        )
    }
    inputs = []
    for worker_id, revision in latest_by_worker.items():
        assessment = assessments.get(revision.revision_id)
        if assessment is None:
            continue
        components = assessment.component_manifest.get("components")
        scope_evidence = assessment.component_manifest.get("scope_evidence")
        if not isinstance(components, dict) or not isinstance(scope_evidence, dict):
            continue
        inputs.append(
            {
                "assessment_sha256": assessment.assessment_sha256,
                "eligible_components": [
                    component
                    for component in ("evidence", "geometry", "portal", "scope")
                    if components.get(component) is True
                ],
                "revision_id": str(revision.revision_id),
                "scope_evidence": scope_evidence,
                "state_sha": revision.state_sha,
                "worker_id": str(worker_id),
            }
        )
    return {"inputs": inputs, "rule_version": SUBMISSION_ASSESSMENT_RULE_V1}


def enqueue_submission_assessment(revision: AnnotationRevision) -> AnalysisJob:
    job, _created = AnalysisJob.objects.get_or_create(
        revision=revision,
        kind=SUBMISSION_ASSESSMENT_KIND,
        rule_version=SUBMISSION_ASSESSMENT_RULE_V1,
        input_sha256=submission_assessment_input_sha(revision),
        defaults={"code_version": SUBMISSION_ASSESSMENT_CODE_V1},
    )
    return job


def _activity_event_records(
    revision: AnnotationRevision, *, event_ids: list[str] | None = None
) -> list[dict[str, object]]:
    events = ActivityEvent.objects.filter(draft_cycle=revision.draft_cycle)
    if event_ids is not None:
        events = events.filter(event_id__in=event_ids)
    return [
        {
            "active_lease_id": (
                None if event.active_lease_id is None else str(event.active_lease_id)
            ),
            "active_time_rule_version": event.active_time_rule_version,
            "client_monotonic_ms": event.client_monotonic_ms,
            "client_session_id": str(event.client_session_id),
            "client_wall_time_ms": event.client_wall_time_ms,
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "focus": event.focus,
            "sequence_no": event.sequence_no,
            "server_received_at": event.server_received_at.isoformat(),
            "visibility": event.visibility,
        }
        for event in events.order_by("event_id")
    ]


def _audit_input_manifest(revision: AnnotationRevision, *, audit_type: str) -> dict[str, object]:
    manifest: dict[str, object] = {
        "audit_type": audit_type,
        "active_time_rule_version": revision.task.active_time_rule_version,
        "meta_copy_version": revision.meta_copy_version,
        "meta_schema_version": revision.meta_schema_version,
        "prediction_artifact_id": (
            None
            if revision.task.prediction_artifact_id is None
            else str(revision.task.prediction_artifact_id)
        ),
        "prediction_artifact_sha256": revision.task.prediction_artifact_sha256,
        "revision_id": str(revision.revision_id),
        "revision_state_sha": revision.state_sha,
        "task_mode": revision.task.mode,
    }
    if audit_type == "time_integrity":
        records = _activity_event_records(revision)
        manifest["activity_event_ids"] = [record["event_id"] for record in records]
        manifest["activity_events_sha256"] = json_sha256(records)
    return manifest


def request_revision_audit(*, revision_id: UUID, audit_type: str) -> tuple[AnalysisJob, bool]:
    if audit_type not in REGISTERED_AUDIT_TYPES:
        raise ValueError("audit_type_unregistered")
    revision = (
        AnnotationRevision.objects.select_related("task").filter(revision_id=revision_id).first()
    )
    if revision is None:
        raise ResourceNotFound
    input_manifest = _audit_input_manifest(revision, audit_type=audit_type)
    input_sha256 = json_sha256(input_manifest)
    job, created = AnalysisJob.objects.get_or_create(
        revision=revision,
        kind=f"audit:{audit_type}",
        rule_version=AUDIT_RULE_V1,
        input_sha256=input_sha256,
        defaults={
            "code_version": AUDIT_CODE_V1,
            "input_manifest": input_manifest,
        },
    )
    if job.input_manifest != input_manifest or job.code_version != AUDIT_CODE_V1:
        raise ValueError("audit_run_input_conflict")
    return job, created


@transaction.atomic
def claim_analysis_job(*, worker_id: str) -> AnalysisJob | None:
    now = timezone.now()
    job = (
        AnalysisJob.objects.select_for_update(skip_locked=True)
        .filter(
            Q(
                status__in=(AnalysisJob.Status.PENDING, AnalysisJob.Status.FAILED),
                available_at__lte=now,
            )
            | Q(
                status=AnalysisJob.Status.RUNNING,
                locked_at__lt=now - JOB_LEASE,
            )
        )
        .order_by("available_at", "created_at", "job_id")
        .first()
    )
    if job is None:
        return None
    job.status = AnalysisJob.Status.RUNNING
    job.attempt_count += 1
    job.locked_at = now
    job.locked_by = worker_id[:128]
    job.save(update_fields=["attempt_count", "locked_at", "locked_by", "status", "updated_at"])
    return job


def _assessment_manifest(revision: AnnotationRevision) -> dict[str, object]:
    canonical: Mapping[str, object]
    try:
        canonical = validate_annotation_submission(
            revision.state,
            meta_schema_version=revision.meta_schema_version,
            task_mode=revision.task.mode or "",
        )
        structure_valid = (
            annotation_state_sha(
                canonical,
                meta_schema_version=revision.meta_schema_version,
                task_mode=revision.task.mode or "",
            )
            == revision.state_sha
        )
    except AnnotationStateError:
        canonical = {}
        structure_valid = False
    pairs = canonical.get("pairs", [])
    portals = canonical.get("portals", [])
    return {
        "components": {
            "evidence": canonical.get("geometry_attempt_status") is not None,
            "geometry": bool(pairs),
            "portal": bool(portals),
            "scope": canonical.get("worker_scope_observation") is not None,
        },
        "scope_evidence": {
            "observation": canonical.get("worker_scope_observation"),
            "reason_codes": canonical.get("scope_reason_codes", []),
        },
        "structure_valid": structure_valid,
    }


def _component_inputs(manifest: dict[str, object]) -> list[dict[str, object]]:
    manifest_inputs = cast(list[dict[str, object]], manifest["inputs"])
    revisions = {
        str(revision.revision_id): revision
        for revision in AnnotationRevision.objects.filter(
            revision_id__in=[item["revision_id"] for item in manifest_inputs]
        )
    }
    return [
        {
            "revision_id": revision_id,
            "state": revisions[revision_id].state,
            "submitted_at": revisions[revision_id].submitted_at.isoformat(),
        }
        for revision_id in (cast(str, item["revision_id"]) for item in manifest_inputs)
    ]


def _not_evaluable_components() -> CanonicalComponentAggregation:
    def empty_component() -> dict[str, object]:
        return {
            "cluster_revision_ids": [],
            "margin": 0,
            "medoid_revision_id": None,
            "primary_support": 0,
            "secondary_support": 0,
            "state": "not_evaluable",
        }

    return cast(
        CanonicalComponentAggregation,
        {"geometry": empty_component(), "portal": empty_component()},
    )


def _terminal_aggregation_state(
    *,
    component_result: CanonicalComponentAggregation,
    consensus_policy: dict[str, object],
    eligible_manifest: dict[str, object],
    scope_state: str,
) -> str:
    if scope_state == "resolved_oos":
        return "resolved"
    inputs = cast(list[dict[str, object]], eligible_manifest["inputs"])
    has_scope_conflict = any(
        cast(dict[str, object], item["scope_evidence"]).get("observation") != "annotatable"
        for item in inputs
    )
    portal_state = component_result["portal"]["state"]
    if (
        not has_scope_conflict
        and component_result["geometry"]["state"] == "resolved"
        and portal_state in {"not_evaluable", "resolved"}
    ):
        return "resolved"
    return "unresolved" if len(inputs) >= cast(int, consensus_policy["k_max"]) else "needs_more"


def _audit_findings(job: AnalysisJob) -> list[str]:
    revision = job.revision
    audit_type = job.kind.removeprefix("audit:")
    if _audit_input_manifest(revision, audit_type=audit_type) != job.input_manifest:
        if audit_type != "time_integrity":
            raise ValueError("audit_input_changed")
        current_manifest = _audit_input_manifest(revision, audit_type=audit_type)
        for field in (
            "audit_type",
            "active_time_rule_version",
            "meta_copy_version",
            "meta_schema_version",
            "prediction_artifact_id",
            "prediction_artifact_sha256",
            "revision_id",
            "revision_state_sha",
            "task_mode",
        ):
            if current_manifest[field] != job.input_manifest[field]:
                raise ValueError("audit_input_changed")
    if audit_type == "canonical_structure":
        try:
            canonical = validate_annotation_submission(
                revision.state,
                meta_schema_version=revision.meta_schema_version,
                task_mode=revision.task.mode or "",
            )
            if (
                annotation_state_sha(
                    canonical,
                    meta_schema_version=revision.meta_schema_version,
                    task_mode=revision.task.mode or "",
                )
                != revision.state_sha
            ):
                return ["revision_state_sha_mismatch"]
        except AnnotationStateError as error:
            return [error.code]
        return []
    if audit_type == "scope_portal":
        try:
            canonical = validate_annotation_submission(
                revision.state,
                meta_schema_version=revision.meta_schema_version,
                task_mode=revision.task.mode or "",
            )
        except AnnotationStateError as error:
            return [error.code]
        scope_findings = []
        if canonical["worker_scope_observation"] != "annotatable" and canonical["portals"]:
            scope_findings.append("scope_portal_conflict")
        if canonical["worker_scope_observation"] == "annotatable" and not canonical["pairs"]:
            scope_findings.append("scope_geometry_missing")
        return scope_findings
    if audit_type == "time_integrity":
        event_ids = cast(list[str], job.input_manifest.get("activity_event_ids", []))
        records = _activity_event_records(revision, event_ids=event_ids)
        if [record["event_id"] for record in records] != event_ids or json_sha256(
            records
        ) != job.input_manifest.get("activity_events_sha256"):
            raise ValueError("audit_input_changed")
        time_findings: set[str] = set()
        streams: dict[str, list[dict[str, object]]] = defaultdict(list)
        for record in records:
            if record["active_time_rule_version"] != revision.task.active_time_rule_version:
                time_findings.add("active_time_rule_mismatch")
            streams[cast(str, record["client_session_id"])].append(record)
        for stream in streams.values():
            previous_monotonic: float | None = None
            previous_wall: int | None = None
            for record in sorted(stream, key=lambda item: cast(int, item["sequence_no"])):
                monotonic = cast(float, record["client_monotonic_ms"])
                wall = cast(int, record["client_wall_time_ms"])
                server_wall = int(
                    datetime.fromisoformat(cast(str, record["server_received_at"])).timestamp()
                    * 1_000
                )
                if not isfinite(monotonic) or monotonic < 0:
                    time_findings.add("client_clock_invalid")
                elif previous_monotonic is not None and monotonic < previous_monotonic:
                    time_findings.add("client_clock_non_monotonic")
                if previous_wall is not None and wall < previous_wall:
                    time_findings.add("wall_clock_non_monotonic")
                if wall > server_wall + ACTIVITY_MAX_FUTURE_WALL_SKEW_MS:
                    time_findings.add("wall_clock_future")
                previous_monotonic = monotonic
                previous_wall = wall
        return sorted(time_findings)
    raise ValueError("audit_type_unregistered")


def _complete_audit_job(job: AnalysisJob) -> AnalysisJob:
    audit_type = job.kind.removeprefix("audit:")
    if (
        audit_type not in REGISTERED_AUDIT_TYPES
        or job.rule_version != AUDIT_RULE_V1
        or job.code_version != AUDIT_CODE_V1
        or json_sha256(job.input_manifest) != job.input_sha256
    ):
        raise ValueError("audit_type_unregistered")
    findings = _audit_findings(job)
    artifact_sha256 = json_sha256(
        {
            "audit_type": audit_type,
            "code_version": job.code_version,
            "findings": findings,
            "input_sha256": job.input_sha256,
            "requires_review": bool(findings),
            "rule_version": job.rule_version,
        }
    )
    artifact, created = AuditArtifact.objects.get_or_create(
        run=job,
        defaults={
            "artifact_sha256": artifact_sha256,
            "audit_type": audit_type,
            "code_version": job.code_version,
            "findings": findings,
            "input_manifest": job.input_manifest,
            "input_sha256": job.input_sha256,
            "requires_review": bool(findings),
            "revision": job.revision,
            "rule_version": job.rule_version,
        },
    )
    if not created and artifact.artifact_sha256 != artifact_sha256:
        raise ValueError("audit_artifact_idempotency_conflict")
    job.status = AnalysisJob.Status.SUCCEEDED
    job.locked_at = None
    job.locked_by = ""
    job.last_error_code = ""
    job.last_error_message = ""
    job.output_manifest = {
        "artifact_id": str(artifact.artifact_id),
        "artifact_sha256": artifact.artifact_sha256,
        "requires_review": artifact.requires_review,
    }
    job.save(
        update_fields=[
            "last_error_code",
            "last_error_message",
            "locked_at",
            "locked_by",
            "output_manifest",
            "status",
            "updated_at",
        ]
    )
    return job


@transaction.atomic
def _complete_analysis_job(*, job_id: UUID, worker_id: str) -> AnalysisJob:
    job = (
        AnalysisJob.objects.select_for_update()
        .select_related("revision__assignment__batch", "revision__task")
        .get(job_id=job_id, status=AnalysisJob.Status.RUNNING, locked_by=worker_id[:128])
    )
    revision = job.revision
    if job.kind.startswith("audit:"):
        return _complete_audit_job(job)
    if job.kind != SUBMISSION_ASSESSMENT_KIND:
        raise ValueError("analysis_kind_unregistered")
    if submission_assessment_input_sha(revision) != job.input_sha256:
        raise ValueError("analysis_input_changed")
    batch = WorkBatch.objects.select_for_update().get(batch_id=revision.assignment.batch_id)
    task = Task.objects.select_for_update().get(task_id=revision.task_id)
    manifest = _assessment_manifest(revision)
    assessment_sha256 = json_sha256(
        {
            "component_manifest": manifest,
            "input_sha256": job.input_sha256,
            "rule_version": job.rule_version,
        }
    )
    assessment, created = SubmissionAssessment.objects.get_or_create(
        revision=revision,
        rule_version=job.rule_version,
        input_sha256=job.input_sha256,
        defaults={
            "assessment_sha256": assessment_sha256,
            "component_manifest": manifest,
            "structure_valid": bool(manifest["structure_valid"]),
        },
    )
    if not created and assessment.assessment_sha256 != assessment_sha256:
        raise ValueError("assessment_idempotency_conflict")
    job.status = AnalysisJob.Status.SUCCEEDED
    job.locked_at = None
    job.locked_by = ""
    job.last_error_code = ""
    job.last_error_message = ""
    eligible_manifest = eligible_task_input_manifest(
        task_id=revision.task_id,
        batch_id=batch.batch_id,
    )
    scope_result = aggregate_scope_evidence(
        policy=validate_scope_policy(batch.scope_policy),
        inputs=cast(list[dict[str, object]], eligible_manifest["inputs"]),
    )
    consensus_policy = validate_consensus_policy(batch.consensus_policy)
    exposure_count = len(cast(list[object], eligible_manifest["inputs"]))
    if scope_result["state"] == "needs_more" and exposure_count >= consensus_policy["k_max"]:
        scope_result["state"] = "unresolved"
    component_result = (
        aggregate_canonical_components(
            inputs=_component_inputs(eligible_manifest),
            policy=consensus_policy,
        )
        if consensus_policy["version"] == CONSENSUS_POLICY_V2
        else _not_evaluable_components()
    )
    terminal_state = _terminal_aggregation_state(
        component_result=component_result,
        consensus_policy=cast(dict[str, object], consensus_policy),
        eligible_manifest=eligible_manifest,
        scope_state=scope_result["state"],
    )
    aggregate_input_sha256 = json_sha256(
        {
            "consensus_policy": batch.consensus_policy,
            "input_manifest": eligible_manifest,
            "scope_policy": batch.scope_policy,
        }
    )
    aggregate, _created = TaskAggregate.objects.update_or_create(
        batch=batch,
        task=task,
        defaults={
            "consensus_policy_version": consensus_policy["version"],
            "geometry_margin": component_result["geometry"]["margin"],
            "geometry_primary_support": component_result["geometry"]["primary_support"],
            "geometry_secondary_support": component_result["geometry"]["secondary_support"],
            "geometry_state": component_result["geometry"]["state"],
            "input_manifest": eligible_manifest,
            "input_sha256": aggregate_input_sha256,
            "policy_version": batch.scope_policy["version"],
            "portal_margin": component_result["portal"]["margin"],
            "portal_primary_support": component_result["portal"]["primary_support"],
            "portal_secondary_support": component_result["portal"]["secondary_support"],
            "portal_state": component_result["portal"]["state"],
            "scope_reason_code": scope_result["reason_code"] or "",
            "scope_state": scope_result["state"],
            "scope_support": scope_result["support"],
            "terminal_state": terminal_state,
        },
    )
    job.output_manifest = {
        "assessment_id": str(assessment.assessment_id),
        "assessment_sha256": assessment.assessment_sha256,
        "eligible_input_manifest": eligible_manifest,
        "geometry_aggregation": component_result["geometry"],
        "portal_aggregation": component_result["portal"],
        "scope_aggregation": scope_result,
        "task_aggregate_id": str(aggregate.aggregate_id),
        "terminal_state": terminal_state,
    }
    if scope_result["state"] == "resolved_oos":
        reason_code = scope_result["reason_code"]
        if reason_code is None:
            raise RuntimeError("Resolved out-of-scope aggregation is missing its reason code.")
        eligibility_artifact, _created = TaskEligibilityArtifact.objects.get_or_create(
            batch=batch,
            task=task,
            policy_version=batch.scope_policy["version"],
            input_sha256=aggregate_input_sha256,
            defaults={
                "input_manifest": eligible_manifest,
                "outcome": "representation_oos",
                "reason_code": reason_code,
                "support": scope_result["support"],
            },
        )
        job.output_manifest["eligibility_artifact_id"] = str(eligibility_artifact.artifact_id)
    geometry_medoid_id = component_result["geometry"]["medoid_revision_id"]
    if terminal_state == "resolved" and geometry_medoid_id is not None:
        portal_medoid_id = component_result["portal"]["medoid_revision_id"]
        consensus_artifact, _created = TaskConsensusArtifact.objects.get_or_create(
            batch=batch,
            task=task,
            policy_version=consensus_policy["version"],
            input_sha256=aggregate_input_sha256,
            defaults={
                "component_manifest": component_result,
                "geometry_medoid_revision_id": geometry_medoid_id,
                "input_manifest": eligible_manifest,
                "portal_medoid_revision_id": portal_medoid_id,
                "similarity_version": consensus_policy["similarity_version"],
            },
        )
        job.output_manifest["task_consensus_artifact_id"] = str(consensus_artifact.artifact_id)
    addition_count = consensus_addition_count(
        policy=consensus_policy,
        exposure_count=exposure_count,
        state=terminal_state,
    )
    submitted_workers = (
        Assignment.objects.filter(
            batch=batch,
            task=task,
            work_state=Assignment.WorkState.SUBMITTED,
        )
        .values("worker_id")
        .distinct()
        .count()
    )
    has_outstanding_assignment = Assignment.objects.filter(
        batch=batch,
        task=task,
        work_state__in=(Assignment.WorkState.ASSIGNED, Assignment.WorkState.IN_PROGRESS),
    ).exists()
    if (
        addition_count
        and exposure_count == submitted_workers
        and not has_outstanding_assignment
        and batch.status == WorkBatch.Status.OPEN
        and task.status == Task.Status.PUBLISHED
    ):
        proposal, _created = AssignmentProposal.objects.get_or_create(
            batch=batch,
            task=task,
            purpose=AssignmentProposal.Purpose.CONSENSUS_ADDITION,
            input_sha256=aggregate_input_sha256,
            defaults={
                "input_manifest": eligible_manifest,
                "policy_version": consensus_policy["version"],
                "requested_count": addition_count,
            },
        )
        job.output_manifest["assignment_proposal_id"] = str(proposal.proposal_id)
    job.save(
        update_fields=[
            "last_error_code",
            "last_error_message",
            "locked_at",
            "locked_by",
            "output_manifest",
            "status",
            "updated_at",
        ]
    )
    return job


@transaction.atomic
def _fail_analysis_job(*, job_id: UUID, worker_id: str, error: Exception) -> AnalysisJob:
    job = AnalysisJob.objects.select_for_update().get(
        job_id=job_id,
        status=AnalysisJob.Status.RUNNING,
        locked_by=worker_id[:128],
    )
    job.status = AnalysisJob.Status.FAILED
    job.available_at = timezone.now() + JOB_RETRY_DELAY
    job.locked_at = None
    job.locked_by = ""
    job.last_error_code = type(error).__name__[:128]
    job.last_error_message = str(error)[:500]
    job.save(
        update_fields=[
            "available_at",
            "last_error_code",
            "last_error_message",
            "locked_at",
            "locked_by",
            "status",
            "updated_at",
        ]
    )
    return job


def process_next_analysis_job(*, worker_id: str) -> AnalysisJob | None:
    job = claim_analysis_job(worker_id=worker_id)
    if job is None:
        return None
    try:
        return _complete_analysis_job(job_id=job.job_id, worker_id=worker_id)
    except AnalysisJob.DoesNotExist:
        return None
    except Exception as error:
        try:
            return _fail_analysis_job(job_id=job.job_id, worker_id=worker_id, error=error)
        except AnalysisJob.DoesNotExist:
            return None


def analysis_job_metrics() -> dict[str, int]:
    return {
        "attempts": int(AnalysisJob.objects.aggregate(total=Sum("attempt_count"))["total"] or 0),
        "backlog": AnalysisJob.objects.filter(
            status__in=(AnalysisJob.Status.PENDING, AnalysisJob.Status.FAILED)
        ).count(),
        "failures": AnalysisJob.objects.filter(status=AnalysisJob.Status.FAILED).count(),
    }
