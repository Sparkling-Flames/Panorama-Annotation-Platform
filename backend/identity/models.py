from __future__ import annotations

from typing import Any, NoReturn
from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        WORKER = "worker", "Worker"

    worker_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.WORKER)
    must_change_password = models.BooleanField(default=True)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.is_superuser:
            self.role = self.Role.ADMIN
            self.must_change_password = False
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Accounts cannot be deleted; disable them instead.")


class AuditEvent(models.Model):
    event_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="audit_actions")
    target_worker = models.ForeignKey(
        User,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="account_audit_events",
    )
    target_type = models.CharField(max_length=64)
    target_id = models.CharField(max_length=128)
    action = models.CharField(max_length=80)
    reason = models.TextField()
    correlation_id = models.UUIDField(default=uuid4, editable=False)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "event_id")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(target_type=""),
                name="identity_audit_target_type_nonempty",
            ),
            models.CheckConstraint(
                condition=~models.Q(target_id=""),
                name="identity_audit_target_id_nonempty",
            ),
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="identity_audit_reason_nonempty",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Audit events are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Audit events are immutable.")


class DataNoticeAcceptance(models.Model):
    acceptance_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    worker = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="data_notice_acceptances",
    )
    notice_version = models.CharField(max_length=64)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("worker", "notice_version"),
                name="identity_notice_acceptance_unique",
            )
        ]


class ActiveWorkspace(models.Model):
    workspace_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    worker = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="active_workspace",
    )
    token = models.UUIDField(default=uuid4, editable=False, unique=True)
    session_key = models.CharField(max_length=40)
    client_instance_id = models.UUIDField()
    tab_id = models.UUIDField()
    lease_expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
