import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("work", "0018_consensus_policy_assignment_proposal")]

    operations = [
        migrations.CreateModel(
            name="OperationalIssue",
            fields=[
                (
                    "issue_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("draft_save", "Draft save"),
                            ("annotation_structure", "Annotation structure"),
                            ("media_delivery", "Media delivery"),
                            ("activity_event", "Activity event"),
                        ],
                        max_length=32,
                    ),
                ),
                ("error_code", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operational_issues",
                        to="work.assignment",
                    ),
                ),
            ],
            options={
                "ordering": ("created_at", "issue_id"),
                "indexes": [
                    models.Index(
                        fields=["kind", "created_at"],
                        name="work_issue_kind_created",
                    )
                ],
            },
        )
    ]
