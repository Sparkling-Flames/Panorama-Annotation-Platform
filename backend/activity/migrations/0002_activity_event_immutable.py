from __future__ import annotations

from typing import Any

from django.db import migrations

POSTGRES_CREATE = (
    """
    CREATE OR REPLACE FUNCTION activity_reject_event_mutation()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Activity events are immutable.' USING ERRCODE = '23000';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER activity_event_immutable
    BEFORE UPDATE OR DELETE ON activity_activityevent
    FOR EACH ROW EXECUTE FUNCTION activity_reject_event_mutation()
    """,
)

SQLITE_CREATE = (
    """
    CREATE TRIGGER activity_event_immutable_update
    BEFORE UPDATE ON activity_activityevent
    BEGIN
        SELECT RAISE(ABORT, 'Activity events are immutable.');
    END
    """,
    """
    CREATE TRIGGER activity_event_immutable_delete
    BEFORE DELETE ON activity_activityevent
    BEGIN
        SELECT RAISE(ABORT, 'Activity events are immutable.');
    END
    """,
)

POSTGRES_DROP = (
    "DROP TRIGGER IF EXISTS activity_event_immutable ON activity_activityevent",
    "DROP FUNCTION IF EXISTS activity_reject_event_mutation()",
)

SQLITE_DROP = (
    "DROP TRIGGER IF EXISTS activity_event_immutable_delete",
    "DROP TRIGGER IF EXISTS activity_event_immutable_update",
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
    dependencies = [("activity", "0001_initial")]

    operations = [migrations.RunPython(create_triggers, drop_triggers)]
