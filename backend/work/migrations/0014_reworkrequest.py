import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("work", "0013_analysisjob_submissionassessment")]

    operations = [
        migrations.CreateModel(
            name="ReworkRequest",
            fields=[
                (
                    "request_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("instruction", models.TextField()),
                ("due_at", models.DateTimeField()),
                ("exposed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="rework_requests",
                        to="identity.user",
                    ),
                ),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="rework_requests",
                        to="work.assignment",
                    ),
                ),
                (
                    "completed_revision",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="completed_rework_request",
                        to="work.annotationrevision",
                    ),
                ),
                (
                    "draft_cycle",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="rework_request",
                        to="work.draftcycle",
                    ),
                ),
                (
                    "initial_submission_revision",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_rework_request",
                        to="work.annotationrevision",
                    ),
                ),
                (
                    "source_adjudication",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="rework_requests",
                        to="work.adjudicatedrevision",
                    ),
                ),
            ],
            options={"ordering": ("created_at", "request_id")},
        )
    ]
