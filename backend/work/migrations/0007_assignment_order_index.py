from typing import Any

from django.db import migrations, models


def assign_order_indexes(apps: Any, schema_editor: Any) -> None:
    Assignment = apps.get_model("work", "Assignment")
    current_group = None
    order_index = 0
    for assignment in Assignment.objects.order_by(
        "batch_id", "worker_id", "created_at", "assignment_id"
    ):
        group = (assignment.batch_id, assignment.worker_id)
        if group != current_group:
            current_group = group
            order_index = 0
        assignment.order_index = order_index
        assignment.save(update_fields=["order_index"])
        order_index += 1


class Migration(migrations.Migration):
    dependencies = [("work", "0006_assignmentskip")]

    operations = [
        migrations.AddField(
            model_name="assignment",
            name="order_index",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(assign_order_indexes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="assignment",
            name="order_index",
            field=models.PositiveIntegerField(),
        ),
        migrations.AddConstraint(
            model_name="assignment",
            constraint=models.UniqueConstraint(
                fields=("batch", "worker", "order_index"),
                name="work_assignment_batch_worker_order_unique",
            ),
        ),
        migrations.AlterModelOptions(
            name="assignment",
            options={"ordering": ("batch_id", "worker_id", "order_index")},
        ),
    ]
