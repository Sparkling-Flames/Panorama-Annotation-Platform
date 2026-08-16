from __future__ import annotations

import uuid
from typing import Any

import django.db.models.deletion
from django.db import migrations, models

POSTGRES_CREATE = (
    """
    CREATE OR REPLACE FUNCTION work_reject_consensus_artifact_mutation()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Task consensus artifacts are immutable.' USING ERRCODE = '23000';
    END;
    $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
    """,
    """
    CREATE TRIGGER work_consensus_artifact_immutable
    BEFORE UPDATE OR DELETE ON work_taskconsensusartifact
    FOR EACH ROW EXECUTE FUNCTION work_reject_consensus_artifact_mutation()
    """,
)

SQLITE_CREATE = (
    """
    CREATE TRIGGER work_consensus_artifact_immutable_update
    BEFORE UPDATE ON work_taskconsensusartifact
    BEGIN
        SELECT RAISE(ABORT, 'Task consensus artifacts are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_consensus_artifact_immutable_delete
    BEFORE DELETE ON work_taskconsensusartifact
    BEGIN
        SELECT RAISE(ABORT, 'Task consensus artifacts are immutable.');
    END
    """,
)

POSTGRES_DROP = (
    "DROP TRIGGER IF EXISTS work_consensus_artifact_immutable ON work_taskconsensusartifact",
    "DROP FUNCTION IF EXISTS work_reject_consensus_artifact_mutation()",
)

SQLITE_DROP = (
    "DROP TRIGGER IF EXISTS work_consensus_artifact_immutable_delete",
    "DROP TRIGGER IF EXISTS work_consensus_artifact_immutable_update",
)


def create_triggers(_apps: Any, schema_editor: Any) -> None:
    for statement in {
        "postgresql": POSTGRES_CREATE,
        "sqlite": SQLITE_CREATE,
    }.get(schema_editor.connection.vendor, ()):
        schema_editor.execute(statement)


def drop_triggers(_apps: Any, schema_editor: Any) -> None:
    for statement in {
        "postgresql": POSTGRES_DROP,
        "sqlite": SQLITE_DROP,
    }.get(schema_editor.connection.vendor, ()):
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("work", "0023_audit_artifact")]

    operations = [
        migrations.AddField(
            model_name="taskaggregate",
            name="consensus_policy_version",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="geometry_margin",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="geometry_primary_support",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="geometry_secondary_support",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="geometry_state",
            field=models.CharField(
                choices=[
                    ("not_evaluable", "Not evaluable"),
                    ("needs_more", "Needs more"),
                    ("resolved", "Resolved"),
                    ("unresolved", "Unresolved"),
                ],
                default="not_evaluable",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="portal_margin",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="portal_primary_support",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="portal_secondary_support",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="portal_state",
            field=models.CharField(
                choices=[
                    ("not_evaluable", "Not evaluable"),
                    ("needs_more", "Needs more"),
                    ("resolved", "Resolved"),
                    ("unresolved", "Unresolved"),
                ],
                default="not_evaluable",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="taskaggregate",
            name="terminal_state",
            field=models.CharField(
                choices=[
                    ("needs_more", "Needs more"),
                    ("resolved", "Resolved"),
                    ("unresolved", "Unresolved"),
                ],
                default="needs_more",
                max_length=24,
            ),
        ),
        migrations.CreateModel(
            name="TaskConsensusArtifact",
            fields=[
                (
                    "artifact_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("policy_version", models.CharField(max_length=64)),
                ("similarity_version", models.CharField(max_length=64)),
                ("input_manifest", models.JSONField()),
                ("input_sha256", models.CharField(max_length=64)),
                ("component_manifest", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="consensus_artifacts",
                        to="work.workbatch",
                    ),
                ),
                (
                    "geometry_medoid_revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="geometry_consensus_artifacts",
                        to="work.annotationrevision",
                    ),
                ),
                (
                    "portal_medoid_revision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="portal_consensus_artifacts",
                        to="work.annotationrevision",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="consensus_artifacts",
                        to="work.task",
                    ),
                ),
            ],
            options={
                "ordering": ("batch_id", "task_id", "created_at", "artifact_id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("batch", "task", "policy_version", "input_sha256"),
                        name="work_consensus_artifact_input_unique",
                    )
                ],
            },
        ),
        migrations.RunPython(create_triggers, drop_triggers),
    ]
