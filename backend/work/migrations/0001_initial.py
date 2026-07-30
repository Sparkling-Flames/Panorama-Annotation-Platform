import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("media", "0006_mediaimportpreview_publication_variant_ids")]

    operations = [
        migrations.CreateModel(
            name="Task",
            fields=[
                (
                    "task_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        blank=True,
                        choices=[("manual", "Manual"), ("semi", "Semi")],
                        max_length=16,
                        null=True,
                    ),
                ),
                ("meta_schema_version", models.CharField(blank=True, max_length=64, null=True)),
                ("meta_copy_version", models.CharField(blank=True, max_length=64, null=True)),
                ("prediction_exposed", models.BooleanField(default=False)),
                ("model_issue_enabled", models.BooleanField(default=False)),
                ("assist_enabled", models.BooleanField(default=False)),
                ("prediction_artifact_id", models.UUIDField(blank=True, null=True)),
                ("external_task_key", models.CharField(blank=True, max_length=256)),
                ("dataset_source", models.CharField(blank=True, max_length=128)),
                ("import_batch_key", models.CharField(blank=True, max_length=256)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("published", "Published"),
                            ("cancelled", "Cancelled"),
                            ("superseded", "Superseded"),
                            ("tombstoned", "Tombstoned"),
                        ],
                        default="draft",
                        max_length=16,
                    ),
                ),
                ("terminal_reason", models.CharField(blank=True, max_length=500)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("tombstoned_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "asset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tasks",
                        to="media.asset",
                    ),
                ),
                (
                    "previous_round_task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="next_round_tasks",
                        to="work.task",
                    ),
                ),
                (
                    "replacement_task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="replaced_tasks",
                        to="work.task",
                    ),
                ),
                (
                    "source_media_import_preview",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="annotation_round_task",
                        to="media.mediaimportpreview",
                    ),
                ),
            ],
            options={"ordering": ("created_at", "task_id")},
        ),
        migrations.CreateModel(
            name="TaskMediaVariant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "media_variant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="media.mediavariant",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="work.task",
                    ),
                ),
            ],
            options={"ordering": ("created_at", "id")},
        ),
        migrations.AddField(
            model_name="task",
            name="allowed_media_variants",
            field=models.ManyToManyField(
                related_name="tasks",
                through="work.TaskMediaVariant",
                to="media.mediavariant",
            ),
        ),
        migrations.AddConstraint(
            model_name="taskmediavariant",
            constraint=models.UniqueConstraint(
                fields=("task", "media_variant"),
                name="work_task_media_variant_unique",
            ),
        ),
    ]
