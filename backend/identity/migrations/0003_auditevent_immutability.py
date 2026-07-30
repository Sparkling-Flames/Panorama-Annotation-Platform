from __future__ import annotations

from typing import Any

from django.db import migrations

CREATE_TRIGGER = """
CREATE OR REPLACE FUNCTION identity_reject_audit_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Audit events are immutable.' USING ERRCODE = '23000';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER identity_audit_event_immutable
BEFORE UPDATE OR DELETE ON identity_auditevent
FOR EACH ROW EXECUTE FUNCTION identity_reject_audit_event_mutation();
"""

DROP_TRIGGER = """
DROP TRIGGER IF EXISTS identity_audit_event_immutable ON identity_auditevent;
DROP FUNCTION IF EXISTS identity_reject_audit_event_mutation();
"""


def create_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE_TRIGGER)


def drop_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP_TRIGGER)


class Migration(migrations.Migration):
    dependencies = [("identity", "0002_activeworkspace")]

    operations = [migrations.RunPython(create_trigger, drop_trigger)]
