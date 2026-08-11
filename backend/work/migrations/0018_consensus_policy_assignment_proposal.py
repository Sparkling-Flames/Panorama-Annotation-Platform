from __future__ import annotations

import uuid
from typing import Any

import django.db.models.deletion
from django.db import migrations, models

import work.scope_aggregation

POSTGRES_CREATE = (
    """
    CREATE OR REPLACE FUNCTION work_reject_frozen_consensus_policy_mutation()
    RETURNS trigger AS $$
    BEGIN
        IF (OLD.policy_frozen_at IS NOT NULL OR NEW.policy_frozen_at IS NOT NULL)
           AND OLD.consensus_policy IS DISTINCT FROM NEW.consensus_policy THEN
            RAISE EXCEPTION 'Frozen consensus policies are immutable.' USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER work_consensus_policy_immutable
    BEFORE UPDATE ON work_workbatch
    FOR EACH ROW EXECUTE FUNCTION work_reject_frozen_consensus_policy_mutation()
    """,
)

SQLITE_SCOPE_CREATE = (
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
)

SQLITE_CREATE = SQLITE_SCOPE_CREATE + (
    """
    CREATE TRIGGER work_consensus_policy_immutable
    BEFORE UPDATE ON work_workbatch
    WHEN (OLD.policy_frozen_at IS NOT NULL OR NEW.policy_frozen_at IS NOT NULL)
         AND OLD.consensus_policy IS NOT NEW.consensus_policy
    BEGIN
        SELECT RAISE(ABORT, 'Frozen consensus policies are immutable.');
    END
    """,
)

POSTGRES_DROP = (
    "DROP TRIGGER IF EXISTS work_consensus_policy_immutable ON work_workbatch",
    "DROP FUNCTION IF EXISTS work_reject_frozen_consensus_policy_mutation()",
)

SQLITE_DROP = (
    "DROP TRIGGER IF EXISTS work_consensus_policy_immutable",
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


def restore_scope_triggers(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "sqlite":
        for statement in SQLITE_SCOPE_CREATE:
            schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("work", "0017_taskaggregate")]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, restore_scope_triggers),
        migrations.AddField(
            model_name="workbatch",
            name="consensus_policy",
            field=models.JSONField(default=work.scope_aggregation.default_consensus_policy),
        ),
        migrations.CreateModel(
            name="AssignmentProposal",
            fields=[
                (
                    "proposal_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "purpose",
                    models.CharField(
                        choices=[("consensus_addition", "Consensus addition")],
                        max_length=32,
                    ),
                ),
                ("requested_count", models.PositiveIntegerField()),
                ("policy_version", models.CharField(max_length=64)),
                ("input_manifest", models.JSONField()),
                ("input_sha256", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assignment_proposals",
                        to="work.workbatch",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assignment_proposals",
                        to="work.task",
                    ),
                ),
            ],
            options={
                "ordering": ("batch_id", "task_id", "created_at", "proposal_id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("batch", "task", "purpose", "input_sha256"),
                        name="work_assignment_proposal_input_unique",
                    )
                ],
            },
        ),
        migrations.RunPython(create_triggers, drop_triggers),
    ]
