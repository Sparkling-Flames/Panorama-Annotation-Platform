import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("work", "0005_revision_immutable"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssignmentSkip",
            fields=[
                (
                    "skip_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("reason_schema_version", models.CharField(max_length=32)),
                (
                    "reason_code",
                    models.CharField(
                        choices=[
                            ("technical_failure", "Technical failure"),
                            ("image_unavailable", "Image unavailable"),
                            ("temporary_worker_issue", "Temporary worker issue"),
                            ("conflict_of_interest", "Conflict of interest"),
                            ("unable_to_complete", "Unable to complete"),
                            ("other", "Other"),
                        ],
                        max_length=32,
                    ),
                ),
                ("reason_text", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assignment",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="skip_record",
                        to="work.assignment",
                    ),
                ),
                (
                    "draft_cycle",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="skip_records",
                        to="work.draftcycle",
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assignment_skips",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
