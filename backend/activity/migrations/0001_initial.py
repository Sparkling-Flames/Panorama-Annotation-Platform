import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("work", "0005_revision_immutable"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActivityEvent",
            fields=[
                (
                    "event_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("client_session_id", models.UUIDField()),
                ("active_lease_id", models.UUIDField(blank=True, null=True)),
                ("sequence_no", models.PositiveBigIntegerField()),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("focus", "Focus"),
                            ("heartbeat", "Heartbeat"),
                            ("idle", "Idle"),
                            ("interaction", "Interaction"),
                            ("visibility", "Visibility"),
                        ],
                        max_length=16,
                    ),
                ),
                ("client_monotonic_ms", models.FloatField()),
                ("server_received_at", models.DateTimeField(auto_now_add=True)),
                (
                    "visibility",
                    models.CharField(
                        choices=[("hidden", "Hidden"), ("visible", "Visible")],
                        max_length=8,
                    ),
                ),
                ("focus", models.BooleanField()),
                ("interaction_type", models.CharField(blank=True, max_length=32, null=True)),
                ("client_build_sha", models.CharField(max_length=64)),
                ("active_time_rule_version", models.CharField(max_length=32)),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activity_events",
                        to="work.assignment",
                    ),
                ),
                (
                    "draft_cycle",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activity_events",
                        to="work.draftcycle",
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activity_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": (
                    "assignment_id",
                    "draft_cycle_id",
                    "client_session_id",
                    "sequence_no",
                ),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("worker", "client_session_id", "sequence_no"),
                        name="activity_worker_session_sequence_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("sequence_no__gte", 1)),
                        name="activity_sequence_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("client_monotonic_ms__gte", 0)),
                        name="activity_monotonic_nonnegative",
                    ),
                ],
            },
        )
    ]
