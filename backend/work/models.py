from __future__ import annotations

from typing import Any, NoReturn
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from identity.models import User
from media.models import Asset, MediaImportPreview, MediaVariant

from .scope_aggregation import default_consensus_policy, default_scope_policy

ACTIVE_TIME_RULE_V1 = "active-time-v1"


class Task(models.Model):
    class Mode(models.TextChoices):
        MANUAL = "manual", "Manual"
        SEMI = "semi", "Semi"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CANCELLED = "cancelled", "Cancelled"
        SUPERSEDED = "superseded", "Superseded"
        TOMBSTONED = "tombstoned", "Tombstoned"

    @classmethod
    def display_policy_for_mode(cls, mode: str | None) -> dict[str, bool] | None:
        if mode == cls.Mode.MANUAL:
            return {
                "assist_enabled": False,
                "model_issue_enabled": False,
                "prediction_exposed": False,
            }
        if mode == cls.Mode.SEMI:
            return {
                "assist_enabled": False,
                "model_issue_enabled": True,
                "prediction_exposed": True,
            }
        return None

    task_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    asset = models.ForeignKey(
        Asset,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="tasks",
    )
    mode = models.CharField(max_length=16, choices=Mode.choices, blank=True, null=True)
    meta_schema_version = models.CharField(max_length=64, blank=True, null=True)
    meta_copy_version = models.CharField(max_length=64, blank=True, null=True)
    active_time_rule_version = models.CharField(max_length=32, default=ACTIVE_TIME_RULE_V1)
    prediction_exposed = models.BooleanField(default=False)
    model_issue_enabled = models.BooleanField(default=False)
    assist_enabled = models.BooleanField(default=False)
    prediction_artifact_id = models.UUIDField(blank=True, null=True)
    prediction_artifact_sha256 = models.CharField(blank=True, max_length=64)
    allowed_media_variants: models.ManyToManyField[MediaVariant, Any] = models.ManyToManyField(
        MediaVariant,
        related_name="tasks",
        through="TaskMediaVariant",
    )
    external_task_key = models.CharField(blank=True, max_length=256)
    dataset_source = models.CharField(blank=True, max_length=128)
    import_batch_key = models.CharField(blank=True, max_length=256)
    previous_round_task = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="next_round_tasks",
    )
    replacement_task = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="replaced_tasks",
    )
    source_media_import_preview = models.OneToOneField(
        MediaImportPreview,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="annotation_round_task",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    terminal_reason = models.CharField(blank=True, max_length=500)
    published_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    tombstoned_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "task_id")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._allocated_task_id = self.task_id

    def clean(self) -> None:
        super().clean()
        if self.status == self.Status.TOMBSTONED:
            if any(
                (
                    self.asset_id,
                    self.mode,
                    self.meta_schema_version,
                    self.meta_copy_version,
                    self.external_task_key,
                    self.dataset_source,
                    self.import_batch_key,
                    self.previous_round_task_id,
                    self.prediction_artifact_id,
                    self.prediction_artifact_sha256,
                    self.source_media_import_preview_id,
                )
            ):
                raise ValidationError("Tombstoned tasks cannot retain contract content.")
            if self.tombstoned_at is None or not self.terminal_reason.strip():
                raise ValidationError("Tombstoned tasks require a timestamp and reason.")
            return

        if (
            self.asset_id is None
            or self.mode is None
            or not self.meta_schema_version
            or not self.meta_copy_version
            or not self.active_time_rule_version
        ):
            raise ValidationError("Active tasks require an asset, mode, and metadata versions.")
        if self.active_time_rule_version != ACTIVE_TIME_RULE_V1:
            raise ValidationError("Unsupported active-time rule version.")
        expected_policy = self.display_policy_for_mode(self.mode)
        if expected_policy != {
            "assist_enabled": self.assist_enabled,
            "model_issue_enabled": self.model_issue_enabled,
            "prediction_exposed": self.prediction_exposed,
        }:
            raise ValidationError("Task display policy must match its mode.")
        if self.mode == self.Mode.MANUAL and (
            self.prediction_artifact_id is not None or self.prediction_artifact_sha256
        ):
            raise ValidationError("Manual tasks cannot bind prediction artifacts.")
        if (self.prediction_artifact_id is None) != (not self.prediction_artifact_sha256):
            raise ValidationError("Prediction artifact ID and hash must be bound together.")
        if self.prediction_artifact_id is not None:
            artifact = PredictionArtifact.objects.filter(
                artifact_id=self.prediction_artifact_id
            ).first()
            if (
                artifact is None
                or artifact.asset_id != self.asset_id
                or artifact.artifact_sha256 != self.prediction_artifact_sha256
            ):
                raise ValidationError("The prediction artifact contract is invalid.")
        if (
            self.previous_round_task_id is not None
            and self.previous_round_task is not None
            and self.previous_round_task.asset_id != self.asset_id
        ):
            raise ValidationError("Annotation rounds must reuse the same asset.")

        if self.status == self.Status.DRAFT:
            if any(
                (self.published_at, self.cancelled_at, self.tombstoned_at, self.replacement_task_id)
            ):
                raise ValidationError("Draft task lifecycle fields are inconsistent.")
        elif self.status == self.Status.PUBLISHED:
            if self.published_at is None or any(
                (self.cancelled_at, self.tombstoned_at, self.replacement_task_id)
            ):
                raise ValidationError("Published task lifecycle fields are inconsistent.")
        elif self.status == self.Status.CANCELLED:
            if (
                self.published_at is None
                or self.cancelled_at is None
                or not self.terminal_reason.strip()
            ):
                raise ValidationError("Cancelled tasks require publication, time, and reason.")
        elif self.status == self.Status.SUPERSEDED:
            if (
                self.published_at is None
                or self.replacement_task_id is None
                or not self.terminal_reason.strip()
            ):
                raise ValidationError("Superseded tasks require a replacement and reason.")
            if self.replacement_task_id == self.task_id:
                raise ValidationError("A task cannot replace itself.")
            replacement_task = self.replacement_task
            if replacement_task is None or replacement_task.status != self.Status.PUBLISHED:
                raise ValidationError("Replacement tasks must be published.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.task_id != self._allocated_task_id:
            raise ValidationError("Task IDs are immutable.")
        if not self._state.adding:
            original = type(self).objects.filter(pk=self._allocated_task_id).first()
            if original is not None:
                changed_fields = {
                    field.attname
                    for field in self._meta.concrete_fields
                    if getattr(self, field.attname) != getattr(original, field.attname)
                }
                if (
                    original.status
                    in {
                        self.Status.CANCELLED,
                        self.Status.SUPERSEDED,
                        self.Status.TOMBSTONED,
                    }
                    and changed_fields
                ):
                    raise ValidationError("Terminal tasks are immutable.")
                if original.status == self.Status.PUBLISHED:
                    allowed_changes_by_status: dict[str, set[str]] = {
                        self.Status.PUBLISHED: set(),
                        self.Status.CANCELLED: {"cancelled_at", "status", "terminal_reason"},
                        self.Status.SUPERSEDED: {
                            "replacement_task_id",
                            "status",
                            "terminal_reason",
                        },
                    }
                    allowed_changes = allowed_changes_by_status.get(self.status)
                    if allowed_changes is None or not changed_fields <= allowed_changes:
                        raise ValidationError("Published task contracts are immutable.")
                elif original.status == self.Status.DRAFT and self.status not in {
                    self.Status.DRAFT,
                    self.Status.PUBLISHED,
                    self.Status.TOMBSTONED,
                }:
                    raise ValidationError("Invalid draft task transition.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Task IDs cannot be deleted or reused; tombstone drafts instead.")


class PredictionArtifact(models.Model):
    artifact_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="prediction_artifacts")
    coordinate_mapping = models.CharField(
        max_length=32,
        choices=MediaVariant.CoordinateMapping.choices,
    )
    state = models.JSONField()
    state_sha256 = models.CharField(max_length=64)
    model_name = models.CharField(max_length=128)
    model_version = models.CharField(max_length=128)
    checkpoint_sha256 = models.CharField(max_length=64)
    inference_config = models.JSONField()
    inference_config_sha256 = models.CharField(max_length=64)
    artifact_sha256 = models.CharField(max_length=64)
    import_source = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "artifact_id")

    def clean(self) -> None:
        super().clean()
        if self.coordinate_mapping != MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY:
            raise ValidationError("Prediction coordinates must use normalized identity mapping.")
        if (
            not self.model_name.strip()
            or not self.model_version.strip()
            or not self.import_source.strip()
        ):
            raise ValidationError("Prediction provenance fields cannot be empty.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Prediction artifacts are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Prediction artifacts are immutable.")


class PredictionImportPreview(models.Model):
    preview_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="+")
    importer_version = models.CharField(max_length=64)
    contract = models.JSONField()
    contract_sha256 = models.CharField(max_length=64)
    raw_output_sha256 = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    published_artifact = models.OneToOneField(
        PredictionArtifact,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "preview_id")


class TaskMediaVariant(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    media_variant = models.ForeignKey(MediaVariant, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("task", "media_variant"),
                name="work_task_media_variant_unique",
            )
        ]
        ordering = ("created_at", "id")

    def clean(self) -> None:
        super().clean()
        if self.task.status != Task.Status.DRAFT:
            raise ValidationError("Published task media bindings are immutable.")
        if self.task.asset_id != self.media_variant.asset_id:
            raise ValidationError("Task media variants must belong to the task asset.")
        if self.media_variant.published_at is None:
            raise ValidationError("Task media variants must already be published.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self.task.status != Task.Status.DRAFT:
            raise ValidationError("Published task media bindings are immutable.")
        return super().delete(*args, **kwargs)


class WorkBatch(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FROZEN = "frozen", "Frozen"
        CLOSED = "closed", "Closed"

    batch_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    consensus_policy = models.JSONField(default=default_consensus_policy)
    scope_policy = models.JSONField(default=default_scope_policy)
    policy_frozen_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "batch_id")


class Assignment(models.Model):
    class QueueState(models.TextChoices):
        READY = "ready", "Ready"
        DEFERRED = "deferred", "Deferred"
        NEEDS_REVISIT = "needs_revisit", "Needs revisit"

    class WorkState(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"
        BLOCKED = "blocked", "Blocked"
        REVOKED = "revoked", "Revoked"

    class ReviewState(models.TextChoices):
        UNREVIEWED = "unreviewed", "Unreviewed"
        ACCEPTED = "accepted", "Accepted"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        ADJUDICATED = "adjudicated", "Adjudicated"
        CLOSED = "closed", "Closed"

    assignment_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(WorkBatch, on_delete=models.PROTECT, related_name="assignments")
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="assignments")
    worker = models.ForeignKey(User, on_delete=models.PROTECT, related_name="assignments")
    order_index = models.PositiveIntegerField()
    queue_state = models.CharField(
        max_length=24,
        choices=QueueState.choices,
        default=QueueState.READY,
    )
    work_state = models.CharField(
        max_length=24,
        choices=WorkState.choices,
        default=WorkState.ASSIGNED,
    )
    review_state = models.CharField(
        max_length=24,
        choices=ReviewState.choices,
        default=ReviewState.UNREVIEWED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("batch_id", "worker_id", "order_index")
        constraints = [
            models.UniqueConstraint(
                fields=("task", "worker"),
                name="work_assignment_task_worker_unique",
            ),
            models.UniqueConstraint(
                fields=("batch", "worker", "order_index"),
                name="work_assignment_batch_worker_order_unique",
            ),
        ]
        indexes = [models.Index(fields=("worker", "batch"), name="work_assign_worker_batch")]

    def clean(self) -> None:
        super().clean()
        if self.worker.role != User.Role.WORKER:
            raise ValidationError("Assignments require a worker account.")
        if self.task.status != Task.Status.PUBLISHED:
            raise ValidationError("Assignments require a published task.")
        if self._state.adding and self.batch.status != WorkBatch.Status.OPEN:
            raise ValidationError("Assignments require an open batch.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class DraftCycle(models.Model):
    cycle_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.PROTECT,
        related_name="draft_cycles",
    )
    cycle_no = models.PositiveIntegerField()
    closed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("assignment_id", "cycle_no")
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "cycle_no"),
                name="work_draft_cycle_assignment_number_unique",
            ),
            models.UniqueConstraint(
                condition=models.Q(closed_at__isnull=True),
                fields=("assignment",),
                name="work_draft_cycle_one_open_per_assignment",
            ),
        ]


class CurrentDraft(models.Model):
    draft_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    draft_cycle = models.OneToOneField(
        DraftCycle,
        on_delete=models.PROTECT,
        related_name="current_draft",
    )
    state = models.JSONField()
    state_sha = models.CharField(max_length=64)
    draft_version = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class BlockReport(models.Model):
    class ReasonCode(models.TextChoices):
        TECHNICAL_FAILURE = "technical_failure", "Technical failure"
        IMAGE_UNAVAILABLE = "image_unavailable", "Image unavailable"
        TEMPORARY_WORKER_ISSUE = "temporary_worker_issue", "Temporary worker issue"
        CONFLICT_OF_INTEREST = "conflict_of_interest", "Conflict of interest"
        UNABLE_TO_COMPLETE = "unable_to_complete", "Unable to complete"
        OTHER = "other", "Other"

    block_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.PROTECT,
        related_name="block_reports",
    )
    worker = models.ForeignKey(User, on_delete=models.PROTECT, related_name="assignment_blocks")
    draft_cycle = models.ForeignKey(
        DraftCycle,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="block_reports",
    )
    reason_schema_version = models.CharField(max_length=32)
    reason_code = models.CharField(max_length=32, choices=ReasonCode.choices)
    reason_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Block reports are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Block reports are immutable.")


class OperationalIssue(models.Model):
    class Kind(models.TextChoices):
        DRAFT_SAVE = "draft_save", "Draft save"
        ANNOTATION_STRUCTURE = "annotation_structure", "Annotation structure"
        MEDIA_DELIVERY = "media_delivery", "Media delivery"
        ACTIVITY_EVENT = "activity_event", "Activity event"

    issue_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.PROTECT,
        related_name="operational_issues",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    error_code = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "issue_id")
        indexes = [models.Index(fields=("kind", "created_at"), name="work_issue_kind_created")]


class MetricSnapshot(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    snapshot_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(
        WorkBatch,
        on_delete=models.PROTECT,
        related_name="metric_snapshots",
    )
    cutoff = models.DateTimeField()
    input_manifest = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    rule_version = models.CharField(max_length=64)
    code_version = models.CharField(max_length=64)
    platform_release_id = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    locked_at = models.DateTimeField(blank=True, null=True)
    locked_by = models.CharField(blank=True, max_length=128)
    support = models.PositiveIntegerField(default=0)
    missing_count = models.PositiveIntegerField(default=0)
    not_evaluable_count = models.PositiveIntegerField(default=0)
    result_manifest = models.JSONField(default=dict)
    last_error_code = models.CharField(blank=True, max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "snapshot_id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "input_sha256", "rule_version", "code_version"),
                name="work_metric_snapshot_input_unique",
            )
        ]
        indexes = [models.Index(fields=("status", "created_at"), name="work_metric_status_created")]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original_status = (
                type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            )
            if original_status == self.Status.SUCCEEDED:
                raise ValidationError("Completed metric snapshots are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Metric snapshots are immutable.")


class BatchExportSnapshot(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    export_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(
        WorkBatch,
        on_delete=models.PROTECT,
        related_name="export_snapshots",
    )
    cutoff = models.DateTimeField()
    input_manifest = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    rule_version = models.CharField(max_length=64)
    code_version = models.CharField(max_length=64)
    platform_release_id = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    locked_at = models.DateTimeField(blank=True, null=True)
    locked_by = models.CharField(blank=True, max_length=128)
    export_manifest = models.JSONField(default=dict)
    export_sha256 = models.CharField(blank=True, max_length=64)
    last_error_code = models.CharField(blank=True, max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "export_id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "input_sha256", "rule_version", "code_version"),
                name="work_batch_export_input_unique",
            )
        ]
        indexes = [models.Index(fields=("status", "created_at"), name="work_export_status_created")]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            original_status = (
                type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            )
            if original_status == self.Status.SUCCEEDED:
                raise ValidationError("Completed batch exports are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Batch exports are immutable.")


class BlockDisposition(models.Model):
    class Action(models.TextChoices):
        REOPEN = "reopen", "Reopen"
        REASSIGN = "reassign", "Reassign"
        TERMINATE = "terminate", "Terminate"

    disposition_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    block_report = models.OneToOneField(
        BlockReport,
        on_delete=models.PROTECT,
        related_name="disposition",
    )
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="block_dispositions")
    action = models.CharField(max_length=16, choices=Action.choices)
    reason = models.TextField()
    replacement_assignment = models.ForeignKey(
        Assignment,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="source_block_dispositions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Block dispositions are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Block dispositions are immutable.")


class AnnotationRevision(models.Model):
    revision_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="revisions")
    worker = models.ForeignKey(User, on_delete=models.PROTECT, related_name="revisions")
    draft_cycle = models.OneToOneField(
        DraftCycle,
        on_delete=models.PROTECT,
        related_name="revision",
    )
    revision_no = models.PositiveIntegerField()
    state = models.JSONField()
    state_sha = models.CharField(max_length=64)
    source_draft_version = models.PositiveIntegerField()
    meta_schema_version = models.CharField(max_length=64)
    meta_copy_version = models.CharField(max_length=64)
    submission_locale = models.CharField(max_length=16)
    platform_release_id = models.CharField(blank=True, max_length=64, null=True)
    client_build_sha = models.CharField(blank=True, max_length=64, null=True)
    viewer_version = models.CharField(blank=True, max_length=64, null=True)
    interaction_contract_version = models.CharField(blank=True, max_length=64, null=True)
    idempotency_key = models.UUIDField()
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("assignment_id", "revision_no")
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "revision_no"),
                name="work_revision_assignment_number_unique",
            ),
            models.UniqueConstraint(
                fields=("assignment", "idempotency_key"),
                name="work_revision_assignment_idempotency_unique",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Annotation revisions are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Annotation revisions are immutable.")


class AnalysisJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    job_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    revision = models.ForeignKey(
        AnnotationRevision,
        on_delete=models.PROTECT,
        related_name="analysis_jobs",
    )
    kind = models.CharField(max_length=64)
    rule_version = models.CharField(max_length=64)
    input_sha256 = models.CharField(max_length=64)
    code_version = models.CharField(max_length=64)
    input_manifest = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(blank=True, null=True)
    locked_by = models.CharField(blank=True, max_length=128)
    last_error_code = models.CharField(blank=True, max_length=128)
    last_error_message = models.CharField(blank=True, max_length=500)
    output_manifest = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("available_at", "created_at", "job_id")
        constraints = [
            models.UniqueConstraint(
                fields=("revision", "kind", "rule_version", "input_sha256"),
                name="work_analysis_job_input_unique",
            )
        ]
        indexes = [models.Index(fields=("status", "available_at"), name="work_job_status_ready")]


class SubmissionAssessment(models.Model):
    assessment_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    revision = models.ForeignKey(
        AnnotationRevision,
        on_delete=models.PROTECT,
        related_name="submission_assessments",
    )
    rule_version = models.CharField(max_length=64)
    input_sha256 = models.CharField(max_length=64)
    structure_valid = models.BooleanField()
    component_manifest = models.JSONField()
    assessment_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("revision_id", "created_at", "assessment_id")
        constraints = [
            models.UniqueConstraint(
                fields=("revision", "rule_version", "input_sha256"),
                name="work_submission_assessment_input_unique",
            )
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Submission assessments are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Submission assessments are immutable.")


class AuditArtifact(models.Model):
    artifact_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    run = models.OneToOneField(
        AnalysisJob,
        on_delete=models.PROTECT,
        related_name="audit_artifact",
    )
    revision = models.ForeignKey(
        AnnotationRevision,
        on_delete=models.PROTECT,
        related_name="audit_artifacts",
    )
    audit_type = models.CharField(max_length=64)
    input_manifest = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    rule_version = models.CharField(max_length=64)
    code_version = models.CharField(max_length=64)
    findings = models.JSONField()
    requires_review = models.BooleanField()
    artifact_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("revision_id", "created_at", "artifact_id")
        constraints = [
            models.UniqueConstraint(
                fields=("revision", "audit_type", "input_sha256"),
                name="work_audit_artifact_input_unique",
            )
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Audit artifacts are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Audit artifacts are immutable.")


class TaskEligibilityArtifact(models.Model):
    artifact_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(
        WorkBatch,
        on_delete=models.PROTECT,
        related_name="eligibility_artifacts",
    )
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="eligibility_artifacts")
    policy_version = models.CharField(max_length=64)
    input_manifest = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    outcome = models.CharField(max_length=32)
    reason_code = models.CharField(max_length=64)
    support = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("batch_id", "task_id", "created_at", "artifact_id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "task", "policy_version", "input_sha256"),
                name="work_eligibility_artifact_input_unique",
            )
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Task eligibility artifacts are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Task eligibility artifacts are immutable.")


class TaskAggregate(models.Model):
    class ScopeState(models.TextChoices):
        NEEDS_MORE = "needs_more", "Needs more"
        RESOLVED_OOS = "resolved_oos", "Resolved OOS"
        UNRESOLVED = "unresolved", "Unresolved"

    class ComponentState(models.TextChoices):
        NOT_EVALUABLE = "not_evaluable", "Not evaluable"
        NEEDS_MORE = "needs_more", "Needs more"
        RESOLVED = "resolved", "Resolved"
        UNRESOLVED = "unresolved", "Unresolved"

    class TerminalState(models.TextChoices):
        NEEDS_MORE = "needs_more", "Needs more"
        RESOLVED = "resolved", "Resolved"
        UNRESOLVED = "unresolved", "Unresolved"

    aggregate_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(
        WorkBatch,
        on_delete=models.PROTECT,
        related_name="task_aggregates",
    )
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="aggregates")
    policy_version = models.CharField(max_length=64)
    input_manifest = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    consensus_policy_version = models.CharField(blank=True, max_length=64)
    scope_state = models.CharField(max_length=24, choices=ScopeState.choices)
    scope_reason_code = models.CharField(blank=True, max_length=64)
    scope_support = models.PositiveIntegerField()
    geometry_state = models.CharField(
        max_length=24,
        choices=ComponentState.choices,
        default=ComponentState.NOT_EVALUABLE,
    )
    geometry_primary_support = models.PositiveIntegerField(default=0)
    geometry_secondary_support = models.PositiveIntegerField(default=0)
    geometry_margin = models.IntegerField(default=0)
    portal_state = models.CharField(
        max_length=24,
        choices=ComponentState.choices,
        default=ComponentState.NOT_EVALUABLE,
    )
    portal_primary_support = models.PositiveIntegerField(default=0)
    portal_secondary_support = models.PositiveIntegerField(default=0)
    portal_margin = models.IntegerField(default=0)
    terminal_state = models.CharField(
        max_length=24,
        choices=TerminalState.choices,
        default=TerminalState.NEEDS_MORE,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("batch_id", "task_id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "task"),
                name="work_task_aggregate_batch_task_unique",
            )
        ]


class TaskConsensusArtifact(models.Model):
    artifact_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(
        WorkBatch,
        on_delete=models.PROTECT,
        related_name="consensus_artifacts",
    )
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="consensus_artifacts")
    policy_version = models.CharField(max_length=64)
    similarity_version = models.CharField(max_length=64)
    input_manifest = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    component_manifest = models.JSONField()
    geometry_medoid_revision = models.ForeignKey(
        AnnotationRevision,
        on_delete=models.PROTECT,
        related_name="geometry_consensus_artifacts",
    )
    portal_medoid_revision = models.ForeignKey(
        AnnotationRevision,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="portal_consensus_artifacts",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("batch_id", "task_id", "created_at", "artifact_id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "task", "policy_version", "input_sha256"),
                name="work_consensus_artifact_input_unique",
            )
        ]

    def clean(self) -> None:
        super().clean()
        medoids = (self.geometry_medoid_revision, self.portal_medoid_revision)
        if any(medoid is not None and medoid.task_id != self.task_id for medoid in medoids):
            raise ValidationError("Consensus medoids must belong to the task.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Task consensus artifacts are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Task consensus artifacts are immutable.")


class AssignmentProposal(models.Model):
    class Purpose(models.TextChoices):
        CONSENSUS_ADDITION = "consensus_addition", "Consensus addition"

    proposal_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(
        WorkBatch,
        on_delete=models.PROTECT,
        related_name="assignment_proposals",
    )
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="assignment_proposals")
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    requested_count = models.PositiveIntegerField()
    policy_version = models.CharField(max_length=64)
    input_manifest = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("batch_id", "task_id", "created_at", "proposal_id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "task", "purpose", "input_sha256"),
                name="work_assignment_proposal_input_unique",
            )
        ]


class ReviewRecord(models.Model):
    class Outcome(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"

    review_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    revision = models.ForeignKey(
        AnnotationRevision,
        on_delete=models.PROTECT,
        related_name="review_records",
    )
    reviewer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="review_records")
    outcome = models.CharField(max_length=24, choices=Outcome.choices)
    reason = models.TextField(blank=True)
    rule_version = models.CharField(max_length=32)
    supersedes = models.OneToOneField(
        "self",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="successor",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("revision_id", "created_at", "review_id")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Review records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Review records are immutable.")


class AdjudicatedRevision(models.Model):
    adjudication_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="adjudicated_revisions")
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="adjudicated_revisions",
    )
    state = models.JSONField()
    state_sha = models.CharField(max_length=64)
    source_revision_ids = models.JSONField()
    meta_schema_version = models.CharField(max_length=64)
    meta_copy_version = models.CharField(max_length=64)
    reason = models.TextField()
    rule_version = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("task_id", "created_at", "adjudication_id")

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Adjudicated revisions are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Adjudicated revisions are immutable.")


class ReworkRequest(models.Model):
    request_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.PROTECT,
        related_name="rework_requests",
    )
    initial_submission_revision = models.OneToOneField(
        AnnotationRevision,
        on_delete=models.PROTECT,
        related_name="source_rework_request",
    )
    source_adjudication = models.ForeignKey(
        AdjudicatedRevision,
        on_delete=models.PROTECT,
        related_name="rework_requests",
    )
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="rework_requests")
    instruction = models.TextField()
    due_at = models.DateTimeField()
    exposed_at = models.DateTimeField(blank=True, null=True)
    draft_cycle = models.OneToOneField(
        DraftCycle,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="rework_request",
    )
    completed_revision = models.OneToOneField(
        AnnotationRevision,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="completed_rework_request",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "request_id")


class GuidanceEvent(models.Model):
    guidance_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.PROTECT,
        related_name="guidance_events",
    )
    source_revision = models.ForeignKey(
        AnnotationRevision,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="guidance_events",
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="admin_guidance_events",
    )
    channel = models.CharField(max_length=64)
    category = models.CharField(max_length=64)
    summary = models.CharField(max_length=500)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    feedback_draft_cycle = models.ForeignKey(
        DraftCycle,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="guidance_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "guidance_id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(feedback_draft_cycle__isnull=True)
                    | models.Q(acknowledged_at__isnull=False)
                ),
                name="work_guidance_exposure_requires_ack",
            )
        ]


class TaskDeliverySelection(models.Model):
    selection_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="delivery_selections")
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="task_delivery_selections",
    )
    worker_revision = models.ForeignKey(
        AnnotationRevision,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="delivery_selections",
    )
    adjudicated_revision = models.ForeignKey(
        AdjudicatedRevision,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="delivery_selections",
    )
    reason = models.TextField()
    supersedes = models.OneToOneField(
        "self",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="successor",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("task_id", "created_at", "selection_id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(worker_revision__isnull=False, adjudicated_revision__isnull=True)
                    | models.Q(worker_revision__isnull=True, adjudicated_revision__isnull=False)
                ),
                name="work_delivery_selection_one_target",
            )
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Task delivery selections are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Task delivery selections are immutable.")
