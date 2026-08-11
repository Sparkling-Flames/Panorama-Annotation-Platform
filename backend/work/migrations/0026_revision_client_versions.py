from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("work", "0025_batchexportsnapshot")]

    operations = [
        migrations.AddField(
            model_name="annotationrevision",
            name="client_build_sha",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="annotationrevision",
            name="interaction_contract_version",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="annotationrevision",
            name="platform_release_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="annotationrevision",
            name="viewer_version",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
