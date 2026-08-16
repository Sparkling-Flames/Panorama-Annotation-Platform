from __future__ import annotations

import uuid
from typing import Any

from django.db import migrations, models

DROP_POSTGRES_TRIGGER = """
DROP TRIGGER IF EXISTS identity_audit_event_immutable ON identity_auditevent;
DROP FUNCTION IF EXISTS identity_reject_audit_event_mutation();
"""

CREATE_POSTGRES_TRIGGER = """
CREATE OR REPLACE FUNCTION identity_reject_audit_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Audit events are immutable.' USING ERRCODE = '23000';
END;
$$ LANGUAGE plpgsql SET search_path = pg_catalog, public;

CREATE TRIGGER identity_audit_event_immutable
BEFORE UPDATE OR DELETE ON identity_auditevent
FOR EACH ROW EXECUTE FUNCTION identity_reject_audit_event_mutation();
"""


def drop_immutability_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP_POSTGRES_TRIGGER)


def create_immutability_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE_POSTGRES_TRIGGER)


def populate_domain_targets(apps: Any, _schema_editor: Any) -> None:
    AuditEvent = apps.get_model("identity", "AuditEvent")
    for event in AuditEvent.objects.select_related("target_worker").iterator():
        event.target_id = str(event.target_worker.worker_id)
        event.reason = event.action
        event.save(update_fields=["reason", "target_id"])


def restore_legacy_worker_targets(apps: Any, _schema_editor: Any) -> None:
    AuditEvent = apps.get_model("identity", "AuditEvent")
    AuditEvent.objects.filter(target_worker__isnull=True).update(
        target_worker_id=models.F("actor_id")
    )


class Migration(migrations.Migration):
    dependencies = [("identity", "0003_auditevent_immutability")]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="target_worker",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.PROTECT,
                related_name="account_audit_events",
                to="identity.user",
            ),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="correlation_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="reason",
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="target_id",
            field=models.CharField(max_length=128, null=True),
        ),
        migrations.AddField(
            model_name="auditevent",
            name="target_type",
            field=models.CharField(default="worker", max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(drop_immutability_trigger, migrations.RunPython.noop),
        migrations.RunPython(populate_domain_targets, restore_legacy_worker_targets),
        migrations.RunPython(create_immutability_trigger, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="auditevent",
            name="reason",
            field=models.TextField(),
        ),
        migrations.AlterField(
            model_name="auditevent",
            name="target_id",
            field=models.CharField(max_length=128),
        ),
        migrations.AddConstraint(
            model_name="auditevent",
            constraint=models.CheckConstraint(
                condition=~models.Q(target_type=""),
                name="identity_audit_target_type_nonempty",
            ),
        ),
        migrations.AddConstraint(
            model_name="auditevent",
            constraint=models.CheckConstraint(
                condition=~models.Q(target_id=""),
                name="identity_audit_target_id_nonempty",
            ),
        ),
        migrations.AddConstraint(
            model_name="auditevent",
            constraint=models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="identity_audit_reason_nonempty",
            ),
        ),
    ]
