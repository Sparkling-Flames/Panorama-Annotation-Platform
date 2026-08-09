from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import models
from identity.models import User
from media.models import Asset, MediaImportPreview, MediaVariant


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
    prediction_exposed = models.BooleanField(default=False)
    model_issue_enabled = models.BooleanField(default=False)
    assist_enabled = models.BooleanField(default=False)
    prediction_artifact_id = models.UUIDField(blank=True, null=True)
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
        ):
            raise ValidationError("Active tasks require an asset, mode, and metadata versions.")
        expected_policy = self.display_policy_for_mode(self.mode)
        if expected_policy != {
            "assist_enabled": self.assist_enabled,
            "model_issue_enabled": self.model_issue_enabled,
            "prediction_exposed": self.prediction_exposed,
        }:
            raise ValidationError("Task display policy must match its mode.")
        if self.mode == self.Mode.MANUAL and self.prediction_artifact_id is not None:
            raise ValidationError("Manual tasks cannot bind prediction artifacts.")
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
        SKIPPED = "skipped", "Skipped"
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
        ordering = ("created_at", "assignment_id")
        constraints = [
            models.UniqueConstraint(
                fields=("task", "worker"),
                name="work_assignment_task_worker_unique",
            )
        ]
        indexes = [models.Index(fields=("worker", "batch"), name="work_assign_worker_batch")]

    @property
    def resource_id(self) -> str:
        return str(self.assignment_id)

    @property
    def owner_worker_id(self) -> UUID:
        return self.worker.worker_id

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
