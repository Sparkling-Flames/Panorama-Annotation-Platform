from __future__ import annotations

import uuid
from typing import Any

import django.db.models.deletion
from django.db import migrations, models

import work.scope_aggregation

POSTGRES_CREATE = (
    """
    CREATE OR REPLACE FUNCTION work_reject_frozen_scope_policy_mutation()
    RETURNS trigger AS $$
    BEGIN
        IF (OLD.policy_frozen_at IS NOT NULL OR NEW.policy_frozen_at IS NOT NULL)
           AND OLD.scope_policy IS DISTINCT FROM NEW.scope_policy THEN
            RAISE EXCEPTION 'Frozen scope policies are immutable.' USING ERRCODE = '23000';
        END IF;
        IF OLD.policy_frozen_at IS NOT NULL
           AND OLD.policy_frozen_at IS DISTINCT FROM NEW.policy_frozen_at THEN
            RAISE EXCEPTION 'Scope policy freeze time is immutable.' USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER work_scope_policy_immutable
    BEFORE UPDATE ON work_workbatch
    FOR EACH ROW EXECUTE FUNCTION work_reject_frozen_scope_policy_mutation()
    """,
    """
    CREATE OR REPLACE FUNCTION work_reject_eligibility_artifact_mutation()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Task eligibility artifacts are immutable.' USING ERRCODE = '23000';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER work_eligibility_artifact_immutable
    BEFORE UPDATE OR DELETE ON work_taskeligibilityartifact
    FOR EACH ROW EXECUTE FUNCTION work_reject_eligibility_artifact_mutation()
    """,
)

SQLITE_CREATE = (
    """
    CREATE TRIGGER work_scope_policy_immutable
    BEFORE UPDATE ON work_workbatch
    WHEN (OLD.policy_frozen_at IS NOT NULL OR NEW.policy_frozen_at IS NOT NULL)
         AND OLD.scope_policy IS NOT NEW.scope_policy
    BEGIN
        SELECT RAISE(ABORT, 'Frozen scope policies are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_scope_policy_freeze_time_immutable
    BEFORE UPDATE ON work_workbatch
    WHEN OLD.policy_frozen_at IS NOT NULL AND OLD.policy_frozen_at IS NOT NEW.policy_frozen_at
    BEGIN
        SELECT RAISE(ABORT, 'Scope policy freeze time is immutable.');
    END
    """,
    """
    CREATE TRIGGER work_eligibility_artifact_immutable_update
    BEFORE UPDATE ON work_taskeligibilityartifact
    BEGIN
        SELECT RAISE(ABORT, 'Task eligibility artifacts are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_eligibility_artifact_immutable_delete
    BEFORE DELETE ON work_taskeligibilityartifact
    BEGIN
        SELECT RAISE(ABORT, 'Task eligibility artifacts are immutable.');
    END
    """,
)

POSTGRES_DROP = (
    "DROP TRIGGER IF EXISTS work_eligibility_artifact_immutable ON work_taskeligibilityartifact",
    "DROP FUNCTION IF EXISTS work_reject_eligibility_artifact_mutation()",
    "DROP TRIGGER IF EXISTS work_scope_policy_immutable ON work_workbatch",
    "DROP FUNCTION IF EXISTS work_reject_frozen_scope_policy_mutation()",
)

SQLITE_DROP = (
    "DROP TRIGGER IF EXISTS work_eligibility_artifact_immutable_delete",
    "DROP TRIGGER IF EXISTS work_eligibility_artifact_immutable_update",
    "DROP TRIGGER IF EXISTS work_scope_policy_freeze_time_immutable",
    "DROP TRIGGER IF EXISTS work_scope_policy_immutable",
)


def create_triggers(_apps: Any, schema_editor: Any) -> None:
    statements = {
        "postgresql": POSTGRES_CREATE,
        "sqlite": SQLITE_CREATE,
    }.get(schema_editor.connection.vendor, ())
    for statement in statements:
        schema_editor.execute(statement)


def drop_triggers(_apps: Any, schema_editor: Any) -> None:
    statements = {
        "postgresql": POSTGRES_DROP,
        "sqlite": SQLITE_DROP,
    }.get(schema_editor.connection.vendor, ())
    for statement in statements:
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("work", "0015_submission_assessment_immutable")]

    operations = [
        migrations.AddField(
            model_name="workbatch",
            name="policy_frozen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workbatch",
            name="scope_policy",
            field=models.JSONField(default=work.scope_aggregation.default_scope_policy),
        ),
        migrations.CreateModel(
            name="TaskEligibilityArtifact",
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
                ("input_manifest", models.JSONField()),
                ("input_sha256", models.CharField(max_length=64)),
                ("outcome", models.CharField(max_length=32)),
                ("reason_code", models.CharField(max_length=64)),
                ("support", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="eligibility_artifacts",
                        to="work.workbatch",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="eligibility_artifacts",
                        to="work.task",
                    ),
                ),
            ],
            options={
                "ordering": ("batch_id", "task_id", "created_at", "artifact_id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("batch", "task", "policy_version", "input_sha256"),
                        name="work_eligibility_artifact_input_unique",
                    )
                ],
            },
        ),
        migrations.RunPython(create_triggers, drop_triggers),
    ]
