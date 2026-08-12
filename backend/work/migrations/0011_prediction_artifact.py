from __future__ import annotations

import uuid
from typing import Any

import django.db.models.deletion
from django.db import migrations, models


def add_prediction_hash(_apps: Any, schema_editor: Any) -> None:
    schema_editor.execute(
        """
        ALTER TABLE work_task
        ADD COLUMN prediction_artifact_sha256 varchar(64)
        NOT NULL DEFAULT ''
        """
    )
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "ALTER TABLE work_task ALTER COLUMN prediction_artifact_sha256 DROP DEFAULT"
        )


def remove_prediction_hash(_apps: Any, schema_editor: Any) -> None:
    schema_editor.execute("ALTER TABLE work_task DROP COLUMN prediction_artifact_sha256")


def create_triggers(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            """
            CREATE OR REPLACE FUNCTION work_reject_prediction_artifact_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Prediction artifacts are immutable.' USING ERRCODE = '23000';
            END;
            $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
            """
        )
        schema_editor.execute(
            """
            CREATE TRIGGER work_prediction_artifact_immutable
            BEFORE UPDATE OR DELETE ON work_predictionartifact
            FOR EACH ROW EXECUTE FUNCTION work_reject_prediction_artifact_mutation()
            """
        )
        schema_editor.execute(
            """
            CREATE OR REPLACE FUNCTION work_validate_task_prediction_contract()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.status <> 'draft' AND
                   OLD.prediction_artifact_sha256 IS DISTINCT FROM NEW.prediction_artifact_sha256
                THEN
                    RAISE EXCEPTION 'Published task prediction contracts are immutable.'
                        USING ERRCODE = '23000';
                END IF;
                IF NEW.status = 'published' AND (
                    (NEW.mode = 'manual' AND (
                        NEW.prediction_artifact_id IS NOT NULL OR
                        NEW.prediction_artifact_sha256 <> ''
                    )) OR
                    (NEW.mode = 'semi' AND NOT EXISTS (
                        SELECT 1 FROM work_predictionartifact artifact
                        WHERE artifact.artifact_id = NEW.prediction_artifact_id
                          AND artifact.asset_id = NEW.asset_id
                          AND artifact.artifact_sha256 = NEW.prediction_artifact_sha256
                    ))
                ) THEN
                    RAISE EXCEPTION 'Invalid task prediction contract.' USING ERRCODE = '23000';
                END IF;
                IF NEW.status = 'tombstoned' AND NEW.prediction_artifact_sha256 <> '' THEN
                    RAISE EXCEPTION 'Tombstoned tasks cannot retain prediction contracts.'
                        USING ERRCODE = '23000';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
            """
        )
        schema_editor.execute(
            """
            CREATE TRIGGER work_task_prediction_contract
            BEFORE UPDATE ON work_task
            FOR EACH ROW EXECUTE FUNCTION work_validate_task_prediction_contract()
            """
        )
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute(
            """
            CREATE TRIGGER work_prediction_artifact_immutable_update
            BEFORE UPDATE ON work_predictionartifact
            BEGIN
                SELECT RAISE(ABORT, 'Prediction artifacts are immutable.');
            END
            """
        )
        schema_editor.execute(
            """
            CREATE TRIGGER work_prediction_artifact_immutable_delete
            BEFORE DELETE ON work_predictionartifact
            BEGIN
                SELECT RAISE(ABORT, 'Prediction artifacts are immutable.');
            END
            """
        )
        schema_editor.execute(
            """
            CREATE TRIGGER work_task_prediction_contract
            BEFORE UPDATE ON work_task
            WHEN (
                (OLD.status <> 'draft' AND
                 OLD.prediction_artifact_sha256 IS NOT NEW.prediction_artifact_sha256) OR
                (NEW.status = 'published' AND (
                    (NEW.mode = 'manual' AND (
                        NEW.prediction_artifact_id IS NOT NULL OR
                        NEW.prediction_artifact_sha256 <> ''
                    )) OR
                    (NEW.mode = 'semi' AND NOT EXISTS (
                        SELECT 1 FROM work_predictionartifact artifact
                        WHERE artifact.artifact_id = NEW.prediction_artifact_id
                          AND artifact.asset_id = NEW.asset_id
                          AND artifact.artifact_sha256 = NEW.prediction_artifact_sha256
                    ))
                )) OR
                (NEW.status = 'tombstoned' AND NEW.prediction_artifact_sha256 <> '')
            )
            BEGIN
                SELECT RAISE(ABORT, 'Invalid task prediction contract.');
            END
            """
        )


def drop_triggers(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP TRIGGER IF EXISTS work_task_prediction_contract ON work_task")
        schema_editor.execute("DROP FUNCTION IF EXISTS work_validate_task_prediction_contract()")
        schema_editor.execute(
            "DROP TRIGGER IF EXISTS work_prediction_artifact_immutable ON work_predictionartifact"
        )
        schema_editor.execute("DROP FUNCTION IF EXISTS work_reject_prediction_artifact_mutation()")
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS work_task_prediction_contract")
        schema_editor.execute("DROP TRIGGER IF EXISTS work_prediction_artifact_immutable_delete")
        schema_editor.execute("DROP TRIGGER IF EXISTS work_prediction_artifact_immutable_update")


class Migration(migrations.Migration):
    dependencies = [("work", "0010_task_active_time_rule")]

    operations = [
        migrations.CreateModel(
            name="PredictionArtifact",
            fields=[
                (
                    "artifact_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "coordinate_mapping",
                    models.CharField(
                        choices=[("normalized_identity", "Normalized identity")],
                        max_length=32,
                    ),
                ),
                ("state", models.JSONField()),
                ("state_sha256", models.CharField(max_length=64)),
                ("model_name", models.CharField(max_length=128)),
                ("model_version", models.CharField(max_length=128)),
                ("checkpoint_sha256", models.CharField(max_length=64)),
                ("inference_config", models.JSONField()),
                ("inference_config_sha256", models.CharField(max_length=64)),
                ("artifact_sha256", models.CharField(max_length=64)),
                ("import_source", models.CharField(max_length=256)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "asset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="prediction_artifacts",
                        to="media.asset",
                    ),
                ),
            ],
            options={"ordering": ("created_at", "artifact_id")},
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(add_prediction_hash, remove_prediction_hash)],
            state_operations=[
                migrations.AddField(
                    model_name="task",
                    name="prediction_artifact_sha256",
                    field=models.CharField(blank=True, max_length=64),
                )
            ],
        ),
        migrations.RunPython(create_triggers, drop_triggers),
    ]
