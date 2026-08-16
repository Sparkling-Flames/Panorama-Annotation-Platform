import uuid
from typing import Any

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

TABLES = (
    "work_reviewrecord",
    "work_adjudicatedrevision",
    "work_taskdeliveryselection",
)


def create_immutable_triggers(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            """
            CREATE OR REPLACE FUNCTION work_reject_review_artifact_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Review and adjudication records are immutable.'
                    USING ERRCODE = '23000';
            END;
            $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
            """
        )
        for table in TABLES:
            schema_editor.execute(
                f"""
                CREATE TRIGGER {table}_immutable
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION work_reject_review_artifact_mutation()
                """
            )
    elif schema_editor.connection.vendor == "sqlite":
        for table in TABLES:
            for operation in ("UPDATE", "DELETE"):
                schema_editor.execute(
                    f"""
                    CREATE TRIGGER {table}_immutable_{operation.lower()}
                    BEFORE {operation} ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, 'Review and adjudication records are immutable.');
                    END
                    """
                )


def drop_immutable_triggers(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        for table in TABLES:
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        schema_editor.execute("DROP FUNCTION IF EXISTS work_reject_review_artifact_mutation()")
    elif schema_editor.connection.vendor == "sqlite":
        for table in TABLES:
            for operation in ("update", "delete"):
                schema_editor.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_{operation}")


class Migration(migrations.Migration):
    dependencies = [("work", "0008_block_report_and_disposition")]

    operations = [
        migrations.CreateModel(
            name="AdjudicatedRevision",
            fields=[
                (
                    "adjudication_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("state", models.JSONField()),
                ("state_sha", models.CharField(max_length=64)),
                ("source_revision_ids", models.JSONField()),
                ("meta_schema_version", models.CharField(max_length=64)),
                ("meta_copy_version", models.CharField(max_length=64)),
                ("reason", models.TextField()),
                ("rule_version", models.CharField(max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="adjudicated_revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="adjudicated_revisions",
                        to="work.task",
                    ),
                ),
            ],
            options={"ordering": ("task_id", "created_at", "adjudication_id")},
        ),
        migrations.CreateModel(
            name="ReviewRecord",
            fields=[
                (
                    "review_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("accepted", "Accepted"),
                            ("changes_requested", "Changes requested"),
                        ],
                        max_length=24,
                    ),
                ),
                ("reason", models.TextField(blank=True)),
                ("rule_version", models.CharField(max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "reviewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="review_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="review_records",
                        to="work.annotationrevision",
                    ),
                ),
                (
                    "supersedes",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="successor",
                        to="work.reviewrecord",
                    ),
                ),
            ],
            options={"ordering": ("revision_id", "created_at", "review_id")},
        ),
        migrations.CreateModel(
            name="TaskDeliverySelection",
            fields=[
                (
                    "selection_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("reason", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="task_delivery_selections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "adjudicated_revision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="delivery_selections",
                        to="work.adjudicatedrevision",
                    ),
                ),
                (
                    "supersedes",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="successor",
                        to="work.taskdeliveryselection",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="delivery_selections",
                        to="work.task",
                    ),
                ),
                (
                    "worker_revision",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="delivery_selections",
                        to="work.annotationrevision",
                    ),
                ),
            ],
            options={
                "ordering": ("task_id", "created_at", "selection_id"),
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                worker_revision__isnull=False,
                                adjudicated_revision__isnull=True,
                            )
                            | models.Q(
                                worker_revision__isnull=True,
                                adjudicated_revision__isnull=False,
                            )
                        ),
                        name="work_delivery_selection_one_target",
                    )
                ],
            },
        ),
        migrations.RunPython(create_immutable_triggers, drop_immutable_triggers),
    ]
