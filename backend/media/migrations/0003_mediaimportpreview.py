from __future__ import annotations

import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0002_media_variant_immutability"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaImportPreview",
            fields=[
                (
                    "preview_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("plan", models.JSONField()),
                (
                    "plan_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[django.core.validators.RegexValidator(regex="^[0-9a-f]{64}$")],
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("publication_created_asset", models.BooleanField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "published_asset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="media.asset",
                    ),
                ),
            ],
            options={
                "ordering": ("created_at", "preview_id"),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("publication_created_asset__isnull", True),
                                ("published_asset__isnull", True),
                                ("published_at__isnull", True),
                            ),
                            models.Q(
                                ("publication_created_asset__isnull", False),
                                ("published_asset__isnull", False),
                                ("published_at__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="media_preview_publication_fields_coherent",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("cancelled_at__isnull", True),
                            ("published_at__isnull", True),
                            _connector="OR",
                        ),
                        name="media_preview_not_cancelled_and_published",
                    ),
                ],
            },
        ),
    ]
