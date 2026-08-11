import uuid
from typing import Any

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def rename_skip_state(apps: Any, schema_editor: Any) -> None:
    Assignment = apps.get_model("work", "Assignment")
    BlockReport = apps.get_model("work", "BlockReport")
    Assignment.objects.filter(work_state="skipped").update(work_state="blocked")
    BlockReport.objects.filter(reason_schema_version="assignment-skip-v1").update(
        reason_schema_version="assignment-block-v1"
    )


class Migration(migrations.Migration):
    dependencies = [("work", "0007_assignment_order_index")]

    operations = [
        migrations.RenameModel(old_name="AssignmentSkip", new_name="BlockReport"),
        migrations.RenameField(
            model_name="blockreport",
            old_name="skip_id",
            new_name="block_id",
        ),
        migrations.AlterField(
            model_name="blockreport",
            name="assignment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="block_reports",
                to="work.assignment",
            ),
        ),
        migrations.AlterField(
            model_name="blockreport",
            name="draft_cycle",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="block_reports",
                to="work.draftcycle",
            ),
        ),
        migrations.AlterField(
            model_name="blockreport",
            name="worker",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="assignment_blocks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(rename_skip_state, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="assignment",
            name="work_state",
            field=models.CharField(
                choices=[
                    ("assigned", "Assigned"),
                    ("in_progress", "In progress"),
                    ("submitted", "Submitted"),
                    ("blocked", "Blocked"),
                    ("revoked", "Revoked"),
                ],
                default="assigned",
                max_length=24,
            ),
        ),
        migrations.CreateModel(
            name="BlockDisposition",
            fields=[
                (
                    "disposition_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("reopen", "Reopen"),
                            ("reassign", "Reassign"),
                            ("terminate", "Terminate"),
                        ],
                        max_length=16,
                    ),
                ),
                ("reason", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="block_dispositions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "block_report",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="disposition",
                        to="work.blockreport",
                    ),
                ),
                (
                    "replacement_assignment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_block_dispositions",
                        to="work.assignment",
                    ),
                ),
            ],
        ),
    ]
