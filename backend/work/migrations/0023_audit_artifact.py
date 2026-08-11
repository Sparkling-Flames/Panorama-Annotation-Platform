from __future__ import annotations

import uuid
from typing import Any

import django.db.models.deletion
from django.db import migrations, models

POSTGRES_CREATE = (
    """
    CREATE OR REPLACE FUNCTION work_reject_analysis_job_input_mutation()
    RETURNS trigger AS $$
    BEGIN
        IF OLD.revision_id IS DISTINCT FROM NEW.revision_id
           OR OLD.kind IS DISTINCT FROM NEW.kind
           OR OLD.rule_version IS DISTINCT FROM NEW.rule_version
           OR OLD.code_version IS DISTINCT FROM NEW.code_version
           OR OLD.input_manifest IS DISTINCT FROM NEW.input_manifest
           OR OLD.input_sha256 IS DISTINCT FROM NEW.input_sha256
        THEN
            RAISE EXCEPTION 'Analysis job inputs are immutable.' USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER work_analysis_job_input_immutable
    BEFORE UPDATE ON work_analysisjob
    FOR EACH ROW EXECUTE FUNCTION work_reject_analysis_job_input_mutation()
    """,
    """
    CREATE OR REPLACE FUNCTION work_reject_audit_artifact_mutation()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Audit artifacts are immutable.' USING ERRCODE = '23000';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER work_audit_artifact_immutable
    BEFORE UPDATE OR DELETE ON work_auditartifact
    FOR EACH ROW EXECUTE FUNCTION work_reject_audit_artifact_mutation()
    """,
)

SQLITE_CREATE = (
    """
    CREATE TRIGGER work_analysis_job_input_immutable
    BEFORE UPDATE ON work_analysisjob
    WHEN OLD.revision_id IS NOT NEW.revision_id
         OR OLD.kind IS NOT NEW.kind
         OR OLD.rule_version IS NOT NEW.rule_version
         OR OLD.code_version IS NOT NEW.code_version
         OR OLD.input_manifest IS NOT NEW.input_manifest
         OR OLD.input_sha256 IS NOT NEW.input_sha256
    BEGIN
        SELECT RAISE(ABORT, 'Analysis job inputs are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_audit_artifact_immutable_update
    BEFORE UPDATE ON work_auditartifact
    BEGIN
        SELECT RAISE(ABORT, 'Audit artifacts are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_audit_artifact_immutable_delete
    BEFORE DELETE ON work_auditartifact
    BEGIN
        SELECT RAISE(ABORT, 'Audit artifacts are immutable.');
    END
    """,
)

POSTGRES_DROP = (
    "DROP TRIGGER IF EXISTS work_audit_artifact_immutable ON work_auditartifact",
    "DROP FUNCTION IF EXISTS work_reject_audit_artifact_mutation()",
    "DROP TRIGGER IF EXISTS work_analysis_job_input_immutable ON work_analysisjob",
    "DROP FUNCTION IF EXISTS work_reject_analysis_job_input_mutation()",
)

SQLITE_DROP = (
    "DROP TRIGGER IF EXISTS work_audit_artifact_immutable_delete",
    "DROP TRIGGER IF EXISTS work_audit_artifact_immutable_update",
    "DROP TRIGGER IF EXISTS work_analysis_job_input_immutable",
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
    dependencies = [("work", "0022_guidanceevent")]

    operations = [
        migrations.AddField(
            model_name="analysisjob",
            name="input_manifest",
            field=models.JSONField(default=dict),
        ),
        migrations.CreateModel(
            name="AuditArtifact",
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
                ("audit_type", models.CharField(max_length=64)),
                ("input_manifest", models.JSONField()),
                ("input_sha256", models.CharField(max_length=64)),
                ("rule_version", models.CharField(max_length=64)),
                ("code_version", models.CharField(max_length=64)),
                ("findings", models.JSONField()),
                ("requires_review", models.BooleanField()),
                ("artifact_sha256", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="audit_artifacts",
                        to="work.annotationrevision",
                    ),
                ),
                (
                    "run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="audit_artifact",
                        to="work.analysisjob",
                    ),
                ),
            ],
            options={
                "ordering": ("revision_id", "created_at", "artifact_id"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("revision", "audit_type", "input_sha256"),
                        name="work_audit_artifact_input_unique",
                    )
                ],
            },
        ),
        migrations.RunPython(create_triggers, drop_triggers),
    ]
