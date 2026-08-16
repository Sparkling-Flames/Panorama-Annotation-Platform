from typing import Any

from django.db import migrations, models


def drop_event_triggers(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "DROP TRIGGER IF EXISTS activity_event_immutable ON activity_activityevent"
        )
        schema_editor.execute("DROP FUNCTION IF EXISTS activity_reject_event_mutation()")
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS activity_event_immutable_delete")
        schema_editor.execute("DROP TRIGGER IF EXISTS activity_event_immutable_update")


def create_event_triggers(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            """
            CREATE OR REPLACE FUNCTION activity_reject_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Activity events are immutable.' USING ERRCODE = '23000';
            END;
            $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
            """
        )
        schema_editor.execute(
            """
            CREATE TRIGGER activity_event_immutable
            BEFORE UPDATE OR DELETE ON activity_activityevent
            FOR EACH ROW EXECUTE FUNCTION activity_reject_event_mutation()
            """
        )
    elif schema_editor.connection.vendor == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            schema_editor.execute(
                f"""
                CREATE TRIGGER activity_event_immutable_{operation.lower()}
                BEFORE {operation} ON activity_activityevent
                BEGIN
                    SELECT RAISE(ABORT, 'Activity events are immutable.');
                END
                """
            )


def backfill_v1_events(apps: Any, _schema_editor: Any) -> None:
    ActivityEvent = apps.get_model("activity", "ActivityEvent")
    ActivityEvent.objects.filter(active_time_rule_version="v1").update(
        active_time_rule_version="active-time-v1"
    )
    for event in ActivityEvent.objects.filter(client_wall_time_ms=0).iterator():
        ActivityEvent.objects.filter(pk=event.pk).update(
            client_wall_time_ms=int(event.server_received_at.timestamp() * 1_000)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("activity", "0002_activity_event_immutable"),
        ("work", "0010_task_active_time_rule"),
    ]

    operations = [
        migrations.RunPython(drop_event_triggers, create_event_triggers),
        migrations.AddField(
            model_name="activityevent",
            name="client_wall_time_ms",
            field=models.BigIntegerField(default=0),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="activityevent",
            constraint=models.CheckConstraint(
                condition=models.Q(client_wall_time_ms__gte=0),
                name="activity_wall_time_nonnegative",
            ),
        ),
        migrations.RunPython(backfill_v1_events, migrations.RunPython.noop),
        migrations.RunPython(create_event_triggers, drop_event_triggers),
    ]
