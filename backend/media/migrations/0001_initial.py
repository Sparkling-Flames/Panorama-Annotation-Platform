import uuid

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Asset",
            fields=[
                (
                    "asset_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("source_key", models.CharField(max_length=512, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("created_at", "asset_id")},
        ),
        migrations.CreateModel(
            name="MediaVariant",
            fields=[
                (
                    "media_variant_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "content_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="content_sha256 must be a lowercase SHA-256 hex digest.",
                                regex="^[0-9a-f]{64}$",
                            )
                        ],
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
                    "role",
                    models.CharField(
                        choices=[
                            ("compressed", "Compressed"),
                            ("high_resolution", "High resolution"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "coordinate_mapping",
                    models.CharField(
                        choices=[("normalized_identity", "Normalized identity")],
                        max_length=32,
                    ),
                ),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "asset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="media_variants",
                        to="media.asset",
                    ),
                ),
                ("source_key", models.CharField(max_length=512, unique=True)),
            ],
            options={
                "ordering": ("created_at", "media_variant_id"),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("height__gt", 0), ("width__gt", 0)),
                        name="media_variant_positive_dimensions",
                    ),
                    models.UniqueConstraint(
                        fields=("asset", "role"),
                        name="media_variant_unique_asset_role",
                    ),
                ],
            },
        ),
    ]
