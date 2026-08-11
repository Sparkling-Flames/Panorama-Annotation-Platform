import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("work", "0011_prediction_artifact"),
    ]

    operations = [
        migrations.CreateModel(
            name="PredictionImportPreview",
            fields=[
                (
                    "preview_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("importer_version", models.CharField(max_length=64)),
                ("contract", models.JSONField()),
                ("contract_sha256", models.CharField(max_length=64)),
                ("raw_output_sha256", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
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
                    "asset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="media.asset",
                    ),
                ),
                (
                    "published_artifact",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="work.predictionartifact",
                    ),
                ),
            ],
            options={"ordering": ("created_at", "preview_id")},
        )
    ]
