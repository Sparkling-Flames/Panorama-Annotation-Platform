from __future__ import annotations

from typing import Any, NoReturn
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

ACTIVITY_MAX_FUTURE_WALL_SKEW_MS = 5 * 60_000


class ActivityEvent(models.Model):
    class EventType(models.TextChoices):
        FOCUS = "focus", "Focus"
        HEARTBEAT = "heartbeat", "Heartbeat"
        IDLE = "idle", "Idle"
        INTERACTION = "interaction", "Interaction"
        VISIBILITY = "visibility", "Visibility"

    class Visibility(models.TextChoices):
        HIDDEN = "hidden", "Hidden"
        VISIBLE = "visible", "Visible"

    event_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    worker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="activity_events",
    )
    assignment = models.ForeignKey(
        "work.Assignment",
        on_delete=models.PROTECT,
        related_name="activity_events",
    )
    draft_cycle = models.ForeignKey(
        "work.DraftCycle",
        on_delete=models.PROTECT,
        related_name="activity_events",
    )
    client_session_id = models.UUIDField()
    active_lease_id = models.UUIDField(blank=True, null=True)
    sequence_no = models.PositiveBigIntegerField()
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    client_monotonic_ms = models.FloatField()
    client_wall_time_ms = models.BigIntegerField()
    server_received_at = models.DateTimeField(auto_now_add=True)
    visibility = models.CharField(max_length=8, choices=Visibility.choices)
    focus = models.BooleanField()
    interaction_type = models.CharField(max_length=32, blank=True, null=True)
    client_build_sha = models.CharField(max_length=64)
    active_time_rule_version = models.CharField(max_length=32)

    class Meta:
        ordering = ("assignment_id", "draft_cycle_id", "client_session_id", "sequence_no")
        constraints = [
            models.UniqueConstraint(
                fields=("worker", "client_session_id", "sequence_no"),
                name="activity_worker_session_sequence_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence_no__gte=1),
                name="activity_sequence_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(client_monotonic_ms__gte=0),
                name="activity_monotonic_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(client_wall_time_ms__gte=0),
                name="activity_wall_time_nonnegative",
            ),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Activity events are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValidationError("Activity events are immutable.")
