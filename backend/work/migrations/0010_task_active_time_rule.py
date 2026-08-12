from typing import Any

from django.db import migrations, models


def add_rule_column(_apps: Any, schema_editor: Any) -> None:
    schema_editor.execute(
        """
        ALTER TABLE work_task
        ADD COLUMN active_time_rule_version varchar(32)
        NOT NULL DEFAULT 'active-time-v1'
        """
    )
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "ALTER TABLE work_task ALTER COLUMN active_time_rule_version DROP DEFAULT"
        )


def remove_rule_column(_apps: Any, schema_editor: Any) -> None:
    schema_editor.execute("ALTER TABLE work_task DROP COLUMN active_time_rule_version")


def create_rule_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            """
            CREATE OR REPLACE FUNCTION work_reject_published_task_rule_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Published task activity rules are immutable.'
                    USING ERRCODE = '23000';
            END;
            $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
            """
        )
        schema_editor.execute(
            """
            CREATE TRIGGER work_task_active_time_rule_immutable
            BEFORE UPDATE OF active_time_rule_version ON work_task
            FOR EACH ROW
            WHEN (
                OLD.status <> 'draft' AND
                OLD.active_time_rule_version IS DISTINCT FROM NEW.active_time_rule_version
            )
            EXECUTE FUNCTION work_reject_published_task_rule_mutation()
            """
        )
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute(
            """
            CREATE TRIGGER work_task_active_time_rule_immutable
            BEFORE UPDATE OF active_time_rule_version ON work_task
            WHEN OLD.status <> 'draft'
              AND OLD.active_time_rule_version IS NOT NEW.active_time_rule_version
            BEGIN
                SELECT RAISE(ABORT, 'Published task activity rules are immutable.');
            END
            """
        )


def drop_rule_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "DROP TRIGGER IF EXISTS work_task_active_time_rule_immutable ON work_task"
        )
        schema_editor.execute("DROP FUNCTION IF EXISTS work_reject_published_task_rule_mutation()")
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS work_task_active_time_rule_immutable")


class Migration(migrations.Migration):
    dependencies = [("work", "0009_review_adjudication")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(add_rule_column, remove_rule_column)],
            state_operations=[
                migrations.AddField(
                    model_name="task",
                    name="active_time_rule_version",
                    field=models.CharField(default="active-time-v1", max_length=32),
                )
            ],
        ),
        migrations.RunPython(create_rule_trigger, drop_rule_trigger),
    ]
