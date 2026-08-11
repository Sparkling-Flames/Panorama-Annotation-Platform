from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("work", "0026_revision_client_versions")]

    operations = [
        migrations.RenameField(
            model_name="metricsnapshot",
            old_name="platform_version",
            new_name="platform_release_id",
        ),
        migrations.RenameField(
            model_name="batchexportsnapshot",
            old_name="platform_version",
            new_name="platform_release_id",
        ),
    ]
