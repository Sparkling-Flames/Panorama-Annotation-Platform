from __future__ import annotations

from typing import Any

import django.core.validators
from django.db import migrations, models

CREATE_TRIGGER = """
CREATE OR REPLACE FUNCTION media_reject_registration_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Registered COS object metadata is immutable.' USING ERRCODE = '23000';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER media_object_registration_immutable
BEFORE UPDATE OR DELETE ON media_mediaobjectregistration
FOR EACH ROW EXECUTE FUNCTION media_reject_registration_mutation();
"""

DROP_TRIGGER = """
DROP TRIGGER IF EXISTS media_object_registration_immutable ON media_mediaobjectregistration;
DROP FUNCTION IF EXISTS media_reject_registration_mutation();
"""


def create_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE_TRIGGER)


def drop_trigger(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP_TRIGGER)


class Migration(migrations.Migration):
    dependencies = [("media", "0003_mediaimportpreview")]

    operations = [
        migrations.CreateModel(
            name="MediaObjectRegistration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("source_key", models.CharField(max_length=512, unique=True)),
                ("version_id", models.CharField(max_length=256)),
                (
                    "content_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator(regex="^[0-9a-f]{64}$")],
                    ),
                ),
                (
                    "content_length",
                    models.PositiveBigIntegerField(
                        validators=[django.core.validators.MinValueValidator(1)]
                    ),
                ),
                (
                    "crc64ecma",
                    models.CharField(
                        max_length=20,
                        validators=[django.core.validators.RegexValidator(regex="^[0-9]{1,20}$")],
                    ),
                ),
                (
                    "width",
                    models.PositiveIntegerField(
                        validators=[django.core.validators.MinValueValidator(1)]
                    ),
                ),
                (
                    "height",
                    models.PositiveIntegerField(
                        validators=[django.core.validators.MinValueValidator(1)]
                    ),
                ),
                (
                    "format",
                    models.CharField(
                        choices=[("jpeg", "JPEG"), ("png", "PNG")],
                        max_length=16,
                    ),
                ),
                (
                    "manifest_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator(regex="^[0-9a-f]{64}$")],
                    ),
                ),
                ("registered_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("source_key",),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("content_length__gt", 0), ("height__gt", 0), ("width__gt", 0)
                        ),
                        name="media_registration_positive_values",
                    )
                ],
            },
        ),
        migrations.RunPython(create_trigger, drop_trigger),
    ]
