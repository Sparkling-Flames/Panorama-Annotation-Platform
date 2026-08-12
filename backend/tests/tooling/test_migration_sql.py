from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace


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
