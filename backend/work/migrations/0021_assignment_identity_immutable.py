from __future__ import annotations

from typing import Any

from django.db import migrations


def create_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            """
            CREATE OR REPLACE FUNCTION work_reject_assignment_identity_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.batch_id IS DISTINCT FROM NEW.batch_id OR
                   OLD.task_id IS DISTINCT FROM NEW.task_id OR
                   OLD.worker_id IS DISTINCT FROM NEW.worker_id OR
                   OLD.order_index IS DISTINCT FROM NEW.order_index THEN
                    RAISE EXCEPTION 'Assignment identity and order are immutable.'
                        USING ERRCODE = '23000';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql SET search_path = pg_catalog, public;

            CREATE TRIGGER work_assignment_identity_immutable
            BEFORE UPDATE ON work_assignment
            FOR EACH ROW EXECUTE FUNCTION work_reject_assignment_identity_mutation();
            """
        )
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute(
            """
            CREATE TRIGGER work_assignment_identity_immutable
            BEFORE UPDATE ON work_assignment
            WHEN OLD.batch_id IS NOT NEW.batch_id OR
                 OLD.task_id IS NOT NEW.task_id OR
                 OLD.worker_id IS NOT NEW.worker_id OR
                 OLD.order_index IS NOT NEW.order_index
            BEGIN
                SELECT RAISE(ABORT, 'Assignment identity and order are immutable.');
            END;
            """
        )


def drop_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "DROP TRIGGER IF EXISTS work_assignment_identity_immutable ON work_assignment"
        )
        schema_editor.execute("DROP FUNCTION IF EXISTS work_reject_assignment_identity_mutation()")
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS work_assignment_identity_immutable")


class Migration(migrations.Migration):
    dependencies = [("work", "0020_metricsnapshot")]

    operations = [migrations.RunPython(create_trigger, drop_trigger)]
