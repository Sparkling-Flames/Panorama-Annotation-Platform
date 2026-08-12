from __future__ import annotations

import uuid
from typing import Any

import django.db.models.deletion
from django.db import migrations, models

POSTGRES_CREATE = (
    """
    CREATE OR REPLACE FUNCTION work_reject_batch_export_mutation()
    RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR OLD.status = 'succeeded' THEN
            RAISE EXCEPTION 'Batch exports are immutable.' USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
    """,
    """
    CREATE TRIGGER work_batch_export_immutable
    BEFORE UPDATE OR DELETE ON work_batchexportsnapshot
    FOR EACH ROW EXECUTE FUNCTION work_reject_batch_export_mutation()
    """,
)

SQLITE_CREATE = (
    """
    CREATE TRIGGER work_batch_export_immutable_update
    BEFORE UPDATE ON work_batchexportsnapshot
    WHEN OLD.status = 'succeeded'
    BEGIN
        SELECT RAISE(ABORT, 'Batch exports are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_batch_export_immutable_delete
    BEFORE DELETE ON work_batchexportsnapshot
    BEGIN
        SELECT RAISE(ABORT, 'Batch exports are immutable.');
    END
    """,
)

POSTGRES_DROP = (
    "DROP TRIGGER IF EXISTS work_batch_export_immutable ON work_batchexportsnapshot",
    "DROP FUNCTION IF EXISTS work_reject_batch_export_mutation()",
)

SQLITE_DROP = (
    "DROP TRIGGER IF EXISTS work_batch_export_immutable_delete",
    "DROP TRIGGER IF EXISTS work_batch_export_immutable_update",
)


def create_triggers(_apps: Any, schema_editor: Any) -> None:
    for statement in {"postgresql": POSTGRES_CREATE, "sqlite": SQLITE_CREATE}.get(
        schema_editor.connection.vendor, ()
    ):
        schema_editor.execute(statement)


def drop_triggers(_apps: Any, schema_editor: Any) -> None:
    for statement in {"postgresql": POSTGRES_DROP, "sqlite": SQLITE_DROP}.get(
        schema_editor.connection.vendor, ()
    ):
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("work", "0024_task_consensus_artifact")]

    operations = [
        migrations.CreateModel(
            name="BatchExportSnapshot",
            fields=[
                (
                    "export_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("cutoff", models.DateTimeField()),
                ("input_manifest", models.JSONField()),
                ("input_sha256", models.CharField(max_length=64)),
                ("rule_version", models.CharField(max_length=64)),
                ("code_version", models.CharField(max_length=64)),
                ("platform_version", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("locked_by", models.CharField(blank=True, max_length=128)),
                ("export_manifest", models.JSONField(default=dict)),
                ("export_sha256", models.CharField(blank=True, max_length=64)),
                ("last_error_code", models.CharField(blank=True, max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="export_snapshots",
                        to="work.workbatch",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at", "export_id"),
                "indexes": [
                    models.Index(
                        fields=["status", "created_at"],
                        name="work_export_status_created",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("batch", "input_sha256", "rule_version", "code_version"),
                        name="work_batch_export_input_unique",
                    )
                ],
            },
        ),
        migrations.RunPython(create_triggers, drop_triggers),
    ]
