import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("work", "0016_scope_policy_eligibility")]

    operations = [
        migrations.CreateModel(
            name="TaskAggregate",
            fields=[
                (
                    "aggregate_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("policy_version", models.CharField(max_length=64)),
                ("input_manifest", models.JSONField()),
                ("input_sha256", models.CharField(max_length=64)),
                (
                    "scope_state",
                    models.CharField(
                        choices=[
                            ("needs_more", "Needs more"),
                            ("resolved_oos", "Resolved OOS"),
                            ("unresolved", "Unresolved"),
                        ],
                        max_length=24,
                    ),
                ),
                ("scope_reason_code", models.CharField(blank=True, max_length=64)),
                ("scope_support", models.PositiveIntegerField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="task_aggregates",
                        to="work.workbatch",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="aggregates",
                        to="work.task",
                    ),
                ),
            ],
            options={
                "ordering": ("batch_id", "task_id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("batch", "task"),
                        name="work_task_aggregate_batch_task_unique",
                    )
                ],
            },
        )
    ]
