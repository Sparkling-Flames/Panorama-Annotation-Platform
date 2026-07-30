from __future__ import annotations

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("media", "0004_mediaobjectregistration")]

    operations = [
        migrations.AddField(
            model_name="mediavariant",
            name="content_crc64ecma",
            field=models.CharField(
                max_length=20,
                validators=[django.core.validators.RegexValidator(regex="^[0-9]{1,20}$")],
            ),
        ),
        migrations.AddField(
            model_name="mediavariant",
            name="content_length",
            field=models.PositiveBigIntegerField(
                validators=[django.core.validators.MinValueValidator(1)]
            ),
        ),
        migrations.AddField(
            model_name="mediavariant",
            name="object_version",
            field=models.CharField(max_length=256),
        ),
        migrations.RemoveConstraint(
            model_name="mediavariant",
            name="media_variant_positive_dimensions",
        ),
        migrations.AddConstraint(
            model_name="mediavariant",
            constraint=models.CheckConstraint(
                condition=models.Q(("content_length__gt", 0), ("height__gt", 0), ("width__gt", 0)),
                name="media_variant_positive_dimensions",
            ),
        ),
    ]
