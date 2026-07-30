from __future__ import annotations

from typing import Any

from django.db import migrations

CREATE_TRIGGER = """
CREATE OR REPLACE FUNCTION media_reject_published_variant_mutation()
RETURNS trigger AS $$
BEGIN
    IF OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION 'Published media variants are immutable.' USING ERRCODE = '23000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER media_variant_immutable_after_publication
BEFORE UPDATE OR DELETE ON media_mediavariant
FOR EACH ROW EXECUTE FUNCTION media_reject_published_variant_mutation();
"""

DROP_TRIGGER = """
DROP TRIGGER IF EXISTS media_variant_immutable_after_publication ON media_mediavariant;
DROP FUNCTION IF EXISTS media_reject_published_variant_mutation();
"""


def create_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE_TRIGGER)


def drop_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP_TRIGGER)


class Migration(migrations.Migration):
    dependencies = [("media", "0001_initial")]

    operations = [
        migrations.RemoveConstraint(
            model_name="mediavariant",
            name="media_variant_unique_asset_role",
        ),
        migrations.RunPython(create_trigger, drop_trigger),
    ]
