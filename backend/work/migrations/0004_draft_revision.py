import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("work", "0003_workbatch_assignment"),
    ]

    operations = [
        migrations.CreateModel(
            name="DraftCycle",
            fields=[
                (
                    "cycle_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("cycle_no", models.PositiveIntegerField()),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="draft_cycles",
                        to="work.assignment",
                    ),
                ),
            ],
            options={
                "ordering": ("assignment_id", "cycle_no"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("assignment", "cycle_no"),
                        name="work_draft_cycle_assignment_number_unique",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("closed_at__isnull", True)),
                        fields=("assignment",),
                        name="work_draft_cycle_one_open_per_assignment",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="CurrentDraft",
            fields=[
                (
                    "draft_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("state", models.JSONField()),
                ("state_sha", models.CharField(max_length=64)),
                ("draft_version", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "draft_cycle",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="current_draft",
                        to="work.draftcycle",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AnnotationRevision",
            fields=[
                (
                    "revision_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("revision_no", models.PositiveIntegerField()),
                ("state", models.JSONField()),
                ("state_sha", models.CharField(max_length=64)),
                ("source_draft_version", models.PositiveIntegerField()),
                ("meta_schema_version", models.CharField(max_length=64)),
                ("meta_copy_version", models.CharField(max_length=64)),
                ("submission_locale", models.CharField(max_length=16)),
                ("idempotency_key", models.UUIDField()),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revisions",
                        to="work.assignment",
                    ),
                ),
                (
                    "draft_cycle",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revision",
                        to="work.draftcycle",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revisions",
                        to="work.task",
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("assignment_id", "revision_no"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("assignment", "revision_no"),
                        name="work_revision_assignment_number_unique",
                    ),
                    models.UniqueConstraint(
                        fields=("assignment", "idempotency_key"),
                        name="work_revision_assignment_idempotency_unique",
                    ),
                ],
            },
        ),
    ]
