import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("work", "0002_task_contract_immutability"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkBatch",
            fields=[
                (
                    "batch_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("frozen", "Frozen"), ("closed", "Closed")],
                        default="open",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("created_at", "batch_id")},
        ),
        migrations.CreateModel(
            name="Assignment",
            fields=[
                (
                    "assignment_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "queue_state",
                    models.CharField(
                        choices=[
                            ("ready", "Ready"),
                            ("deferred", "Deferred"),
                            ("needs_revisit", "Needs revisit"),
                        ],
                        default="ready",
                        max_length=24,
                    ),
                ),
                (
                    "work_state",
                    models.CharField(
                        choices=[
                            ("assigned", "Assigned"),
                            ("in_progress", "In progress"),
                            ("submitted", "Submitted"),
                            ("skipped", "Skipped"),
                            ("revoked", "Revoked"),
                        ],
                        default="assigned",
                        max_length=24,
                    ),
                ),
                (
                    "review_state",
                    models.CharField(
                        choices=[
                            ("unreviewed", "Unreviewed"),
                            ("accepted", "Accepted"),
                            ("changes_requested", "Changes requested"),
                            ("adjudicated", "Adjudicated"),
                            ("closed", "Closed"),
                        ],
                        default="unreviewed",
                        max_length=24,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assignments",
                        to="work.workbatch",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assignments",
                        to="work.task",
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("created_at", "assignment_id"),
                "indexes": [
                    models.Index(
                        fields=["worker", "batch"],
                        name="work_assign_worker_batch",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("task", "worker"),
                        name="work_assignment_task_worker_unique",
                    )
                ],
            },
        ),
    ]
