import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("identity", "0004_auditevent_domain_target")]

    operations = [
        migrations.CreateModel(
            name="DataNoticeAcceptance",
            fields=[
                (
                    "acceptance_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("notice_version", models.CharField(max_length=64)),
                ("accepted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "worker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="data_notice_acceptances",
                        to="identity.user",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("worker", "notice_version"),
                        name="identity_notice_acceptance_unique",
                    )
                ]
            },
        )
    ]
