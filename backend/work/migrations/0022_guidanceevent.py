import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("work", "0021_assignment_identity_immutable")]

    operations = [
        migrations.CreateModel(
            name="GuidanceEvent",
            fields=[
                (
                    "guidance_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("channel", models.CharField(max_length=64)),
                ("category", models.CharField(max_length=64)),
                ("summary", models.CharField(max_length=500)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admin_guidance_events",
                        to="identity.user",
                    ),
                ),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="guidance_events",
                        to="work.assignment",
                    ),
                ),
                (
                    "feedback_draft_cycle",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="guidance_events",
                        to="work.draftcycle",
                    ),
                ),
                (
                    "source_revision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="guidance_events",
                        to="work.annotationrevision",
                    ),
                ),
            ],
            options={
                "ordering": ("created_at", "guidance_id"),
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(("feedback_draft_cycle__isnull", True))
                            | models.Q(("acknowledged_at__isnull", False))
                        ),
                        name="work_guidance_exposure_requires_ack",
                    )
                ],
            },
        )
    ]
