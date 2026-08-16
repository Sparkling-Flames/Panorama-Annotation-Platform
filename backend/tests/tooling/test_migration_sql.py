from __future__ import annotations

import re
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TRIGGER_FUNCTIONS = {
    "activity_reject_event_mutation",
    "identity_reject_audit_event_mutation",
    "media_reject_published_variant_mutation",
    "media_reject_registration_mutation",
    "work_reject_analysis_job_input_mutation",
    "work_reject_assignment_identity_mutation",
    "work_reject_audit_artifact_mutation",
    "work_reject_batch_export_mutation",
    "work_reject_consensus_artifact_mutation",
    "work_reject_eligibility_artifact_mutation",
    "work_reject_frozen_consensus_policy_mutation",
    "work_reject_frozen_scope_policy_mutation",
    "work_reject_metric_snapshot_mutation",
    "work_reject_prediction_artifact_mutation",
    "work_reject_published_task_rule_mutation",
    "work_reject_review_artifact_mutation",
    "work_reject_revision_mutation",
    "work_reject_submission_assessment_mutation",
    "work_reject_task_contract_mutation",
    "work_reject_task_media_mutation",
    "work_validate_task_prediction_contract",
}
FUNCTION_SQL = re.compile(
    r"CREATE OR REPLACE FUNCTION\s+(?P<name>[a-z0-9_]+)\(\).*?"
    r"\$\$\s+LANGUAGE\s+plpgsql(?P<options>[^;\n]*)",
    re.DOTALL,
)


class RecordingSchemaEditor:
    def __init__(self, vendor: str) -> None:
        self.connection = SimpleNamespace(vendor=vendor)
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(sql)


def test_assignment_identity_trigger_rollback_uses_postgresql_drop_syntax() -> None:
    migration = import_module("work.migrations.0021_assignment_identity_immutable")
    schema_editor = RecordingSchemaEditor("postgresql")

    migration.drop_trigger(None, schema_editor)

    assert schema_editor.statements == [
        "DROP TRIGGER IF EXISTS work_assignment_identity_immutable ON work_assignment",
        "DROP FUNCTION IF EXISTS work_reject_assignment_identity_mutation()",
    ]


def test_assignment_identity_trigger_rollback_uses_sqlite_drop_syntax() -> None:
    migration = import_module("work.migrations.0021_assignment_identity_immutable")
    schema_editor = RecordingSchemaEditor("sqlite")

    migration.drop_trigger(None, schema_editor)

    assert schema_editor.statements == ["DROP TRIGGER IF EXISTS work_assignment_identity_immutable"]


def test_all_postgresql_trigger_functions_pin_their_search_path() -> None:
    definitions: dict[str, str] = {}
    for app_name in ("activity", "identity", "media", "work"):
        for path in (BACKEND_ROOT / app_name / "migrations").glob("*.py"):
            for match in FUNCTION_SQL.finditer(path.read_text(encoding="utf-8")):
                definitions[match.group("name")] = match.group("options")

    assert set(definitions) == EXPECTED_TRIGGER_FUNCTIONS
    assert all(
        "SET search_path = pg_catalog, public" in options for options in definitions.values()
    )


def test_supabase_security_hardening_migration_revokes_api_access() -> None:
    migration = import_module("work.migrations.0028_harden_postgres_security")
    schema_editor = RecordingSchemaEditor("postgresql")

    migration.harden_postgres_security(None, schema_editor)

    assert schema_editor.statements[:-1] == [
        f"ALTER FUNCTION public.{name}() SET search_path = pg_catalog, public"
        for name in migration.TRIGGER_FUNCTION_NAMES
    ]
    assert schema_editor.statements[-1] == migration.REVOKE_DATA_API_PRIVILEGES
    assert "REVOKE ALL PRIVILEGES ON ALL TABLES" in schema_editor.statements[-1]
    assert "ALTER DEFAULT PRIVILEGES" in schema_editor.statements[-1]
    assert "anon" in schema_editor.statements[-1]
    assert "authenticated" in schema_editor.statements[-1]
    assert "service_role" in schema_editor.statements[-1]
    assert "%%I" in schema_editor.statements[-1]
    assert " FROM %I" not in schema_editor.statements[-1].replace("%%I", "")


def test_supabase_security_hardening_is_a_noop_outside_postgresql() -> None:
    migration = import_module("work.migrations.0028_harden_postgres_security")
    schema_editor = RecordingSchemaEditor("sqlite")

    migration.harden_postgres_security(None, schema_editor)

    assert schema_editor.statements == []
