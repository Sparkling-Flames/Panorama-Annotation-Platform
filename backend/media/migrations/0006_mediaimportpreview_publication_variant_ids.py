from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("media", "0005_mediavariant_cos_identity")]

    operations = [
        migrations.AddField(
            model_name="mediaimportpreview",
            name="publication_created_media_variant_ids",
            field=models.JSONField(default=list),
        )
    ]
