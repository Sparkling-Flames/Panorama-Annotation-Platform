from __future__ import annotations

from typing import Any

from django.db import migrations

POSTGRES_CREATE = (
    """
    CREATE OR REPLACE FUNCTION work_reject_submission_assessment_mutation()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Submission assessments are immutable.' USING ERRCODE = '23000';
    END;
    $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
    """,
    """
    CREATE TRIGGER work_submission_assessment_immutable
    BEFORE UPDATE OR DELETE ON work_submissionassessment
    FOR EACH ROW EXECUTE FUNCTION work_reject_submission_assessment_mutation()
    """,
)

SQLITE_CREATE = (
    """
    CREATE TRIGGER work_submission_assessment_immutable_update
    BEFORE UPDATE ON work_submissionassessment
    BEGIN
        SELECT RAISE(ABORT, 'Submission assessments are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_submission_assessment_immutable_delete
    BEFORE DELETE ON work_submissionassessment
    BEGIN
        SELECT RAISE(ABORT, 'Submission assessments are immutable.');
    END
    """,
)

POSTGRES_DROP = (
    "DROP TRIGGER IF EXISTS work_submission_assessment_immutable ON work_submissionassessment",
    "DROP FUNCTION IF EXISTS work_reject_submission_assessment_mutation()",
)

SQLITE_DROP = (
    "DROP TRIGGER IF EXISTS work_submission_assessment_immutable_delete",
    "DROP TRIGGER IF EXISTS work_submission_assessment_immutable_update",
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
    dependencies = [("work", "0014_reworkrequest")]

    operations = [migrations.RunPython(create_triggers, drop_triggers)]
