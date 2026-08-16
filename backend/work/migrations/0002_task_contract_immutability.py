from __future__ import annotations

from typing import Any

from django.db import migrations

POSTGRES_CREATE = (
    """
    CREATE OR REPLACE FUNCTION work_reject_task_contract_mutation()
    RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Task IDs cannot be deleted.' USING ERRCODE = '23000';
        END IF;
        IF OLD.task_id IS DISTINCT FROM NEW.task_id THEN
            RAISE EXCEPTION 'Task IDs are immutable.' USING ERRCODE = '23000';
        END IF;
        IF OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'Task creation time is immutable.' USING ERRCODE = '23000';
        END IF;
        IF OLD.status IN ('cancelled', 'superseded', 'tombstoned') THEN
            RAISE EXCEPTION 'Terminal tasks are immutable.' USING ERRCODE = '23000';
        END IF;
        IF OLD.status = 'published' THEN
            IF ROW(
                OLD.asset_id,
                OLD.mode,
                OLD.meta_schema_version,
                OLD.meta_copy_version,
                OLD.prediction_exposed,
                OLD.model_issue_enabled,
                OLD.assist_enabled,
                OLD.prediction_artifact_id,
                OLD.external_task_key,
                OLD.dataset_source,
                OLD.import_batch_key,
                OLD.previous_round_task_id,
                OLD.source_media_import_preview_id,
                OLD.published_at
            ) IS DISTINCT FROM ROW(
                NEW.asset_id,
                NEW.mode,
                NEW.meta_schema_version,
                NEW.meta_copy_version,
                NEW.prediction_exposed,
                NEW.model_issue_enabled,
                NEW.assist_enabled,
                NEW.prediction_artifact_id,
                NEW.external_task_key,
                NEW.dataset_source,
                NEW.import_batch_key,
                NEW.previous_round_task_id,
                NEW.source_media_import_preview_id,
                NEW.published_at
            ) THEN
                RAISE EXCEPTION 'Published task contracts are immutable.' USING ERRCODE = '23000';
            END IF;
            IF NEW.status = 'published' THEN
                IF ROW(
                    OLD.terminal_reason,
                    OLD.cancelled_at,
                    OLD.tombstoned_at,
                    OLD.replacement_task_id
                ) IS DISTINCT FROM ROW(
                    NEW.terminal_reason,
                    NEW.cancelled_at,
                    NEW.tombstoned_at,
                    NEW.replacement_task_id
                ) THEN
                    RAISE EXCEPTION 'Published task lifecycle fields are immutable.' USING ERRCODE = '23000';
                END IF;
            ELSIF NEW.status = 'cancelled' THEN
                IF NEW.cancelled_at IS NULL OR BTRIM(NEW.terminal_reason) = '' OR
                   NEW.tombstoned_at IS NOT NULL OR NEW.replacement_task_id IS NOT NULL THEN
                    RAISE EXCEPTION 'Invalid task cancellation.' USING ERRCODE = '23000';
                END IF;
            ELSIF NEW.status = 'superseded' THEN
                IF NEW.replacement_task_id IS NULL OR NEW.replacement_task_id = OLD.task_id OR
                   NOT EXISTS (
                       SELECT 1 FROM work_task replacement
                       WHERE replacement.task_id = NEW.replacement_task_id
                         AND replacement.status = 'published'
                   ) OR BTRIM(NEW.terminal_reason) = '' OR
                   NEW.cancelled_at IS NOT NULL OR NEW.tombstoned_at IS NOT NULL THEN
                    RAISE EXCEPTION 'Invalid task supersession.' USING ERRCODE = '23000';
                END IF;
            ELSE
                RAISE EXCEPTION 'Published tasks cannot be reopened.' USING ERRCODE = '23000';
            END IF;
        ELSIF OLD.status = 'draft' THEN
            IF NEW.status = 'draft' THEN
                IF NEW.published_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR
                   NEW.tombstoned_at IS NOT NULL OR NEW.replacement_task_id IS NOT NULL OR
                   BTRIM(NEW.terminal_reason) <> '' THEN
                    RAISE EXCEPTION 'Invalid draft lifecycle fields.' USING ERRCODE = '23000';
                END IF;
            ELSIF NEW.status = 'published' THEN
                IF NEW.published_at IS NULL OR NEW.cancelled_at IS NOT NULL OR
                   NEW.tombstoned_at IS NOT NULL OR NEW.replacement_task_id IS NOT NULL OR
                   BTRIM(NEW.terminal_reason) <> '' THEN
                    RAISE EXCEPTION 'Invalid task publication.' USING ERRCODE = '23000';
                END IF;
            ELSIF NEW.status = 'tombstoned' THEN
                IF NEW.asset_id IS NOT NULL OR NEW.mode IS NOT NULL OR
                   NEW.meta_schema_version IS NOT NULL OR NEW.meta_copy_version IS NOT NULL OR
                   NEW.prediction_exposed OR NEW.model_issue_enabled OR NEW.assist_enabled OR
                   NEW.prediction_artifact_id IS NOT NULL OR NEW.external_task_key <> '' OR
                   NEW.dataset_source <> '' OR NEW.import_batch_key <> '' OR
                   NEW.previous_round_task_id IS NOT NULL OR NEW.replacement_task_id IS NOT NULL OR
                   NEW.source_media_import_preview_id IS NOT NULL OR
                   NEW.published_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR
                   NEW.tombstoned_at IS NULL OR BTRIM(NEW.terminal_reason) = '' OR EXISTS (
                       SELECT 1 FROM work_taskmediavariant WHERE task_id = OLD.task_id
                   ) THEN
                    RAISE EXCEPTION 'Invalid task tombstone.' USING ERRCODE = '23000';
                END IF;
            ELSE
                RAISE EXCEPTION 'Invalid draft task transition.' USING ERRCODE = '23000';
            END IF;
        ELSE
            RAISE EXCEPTION 'Invalid task lifecycle state.' USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
    """,
    """
    CREATE TRIGGER work_task_contract_immutable
    BEFORE UPDATE OR DELETE ON work_task
    FOR EACH ROW EXECUTE FUNCTION work_reject_task_contract_mutation()
    """,
    """
    CREATE OR REPLACE FUNCTION work_reject_task_media_mutation()
    RETURNS trigger AS $$
    BEGIN
        IF TG_OP IN ('UPDATE', 'DELETE') AND EXISTS (
            SELECT 1 FROM work_task
            WHERE task_id = OLD.task_id
              AND status <> 'draft'
        ) THEN
            RAISE EXCEPTION 'Published task media bindings are immutable.' USING ERRCODE = '23000';
        END IF;
        IF TG_OP IN ('UPDATE', 'INSERT') AND EXISTS (
            SELECT 1 FROM work_task
            WHERE task_id = NEW.task_id
              AND status <> 'draft'
        ) THEN
            RAISE EXCEPTION 'Published task media bindings are immutable.' USING ERRCODE = '23000';
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql SET search_path = pg_catalog, public
    """,
    """
    CREATE TRIGGER work_task_media_immutable
    BEFORE INSERT OR UPDATE OR DELETE ON work_taskmediavariant
    FOR EACH ROW EXECUTE FUNCTION work_reject_task_media_mutation()
    """,
)

SQLITE_CREATE = (
    """
    CREATE TRIGGER work_task_preserve_id
    BEFORE DELETE ON work_task
    BEGIN
        SELECT RAISE(ABORT, 'Task IDs cannot be deleted.');
    END
    """,
    """
    CREATE TRIGGER work_task_id_immutable
    BEFORE UPDATE OF task_id ON work_task
    WHEN OLD.task_id IS NOT NEW.task_id
    BEGIN
        SELECT RAISE(ABORT, 'Task IDs are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_task_terminal_immutable
    BEFORE UPDATE ON work_task
    WHEN OLD.status IN ('cancelled', 'superseded', 'tombstoned')
    BEGIN
        SELECT RAISE(ABORT, 'Terminal tasks are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_task_contract_immutable
    BEFORE UPDATE ON work_task
    WHEN OLD.status = 'published' AND (
        OLD.created_at IS NOT NEW.created_at OR
        OLD.asset_id IS NOT NEW.asset_id OR
        OLD.mode IS NOT NEW.mode OR
        OLD.meta_schema_version IS NOT NEW.meta_schema_version OR
        OLD.meta_copy_version IS NOT NEW.meta_copy_version OR
        OLD.prediction_exposed IS NOT NEW.prediction_exposed OR
        OLD.model_issue_enabled IS NOT NEW.model_issue_enabled OR
        OLD.assist_enabled IS NOT NEW.assist_enabled OR
        OLD.prediction_artifact_id IS NOT NEW.prediction_artifact_id OR
        OLD.external_task_key IS NOT NEW.external_task_key OR
        OLD.dataset_source IS NOT NEW.dataset_source OR
        OLD.import_batch_key IS NOT NEW.import_batch_key OR
        OLD.previous_round_task_id IS NOT NEW.previous_round_task_id OR
        OLD.source_media_import_preview_id IS NOT NEW.source_media_import_preview_id OR
        OLD.published_at IS NOT NEW.published_at OR
        NEW.status NOT IN ('published', 'cancelled', 'superseded') OR
        (NEW.status = 'published' AND (
            OLD.terminal_reason IS NOT NEW.terminal_reason OR
            OLD.cancelled_at IS NOT NEW.cancelled_at OR
            OLD.tombstoned_at IS NOT NEW.tombstoned_at OR
            OLD.replacement_task_id IS NOT NEW.replacement_task_id
        )) OR
        (NEW.status = 'cancelled' AND (
            NEW.cancelled_at IS NULL OR TRIM(NEW.terminal_reason) = '' OR
            NEW.tombstoned_at IS NOT NULL OR NEW.replacement_task_id IS NOT NULL
        )) OR
        (NEW.status = 'superseded' AND (
            NEW.replacement_task_id IS NULL OR NEW.replacement_task_id = OLD.task_id OR
            NOT EXISTS (
                SELECT 1 FROM work_task replacement
                WHERE replacement.task_id = NEW.replacement_task_id
                  AND replacement.status = 'published'
            ) OR TRIM(NEW.terminal_reason) = '' OR
            NEW.cancelled_at IS NOT NULL OR NEW.tombstoned_at IS NOT NULL
        ))
    )
    BEGIN
        SELECT RAISE(ABORT, 'Published task contracts are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_task_draft_transition
    BEFORE UPDATE ON work_task
    WHEN OLD.status = 'draft' AND (
        OLD.created_at IS NOT NEW.created_at OR
        NEW.status NOT IN ('draft', 'published', 'tombstoned') OR
        (NEW.status = 'draft' AND (
            NEW.published_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR
            NEW.tombstoned_at IS NOT NULL OR NEW.replacement_task_id IS NOT NULL OR
            TRIM(NEW.terminal_reason) <> ''
        )) OR
        (NEW.status = 'published' AND (
            NEW.published_at IS NULL OR NEW.cancelled_at IS NOT NULL OR
            NEW.tombstoned_at IS NOT NULL OR NEW.replacement_task_id IS NOT NULL OR
            TRIM(NEW.terminal_reason) <> ''
        )) OR
        (NEW.status = 'tombstoned' AND (
            NEW.asset_id IS NOT NULL OR NEW.mode IS NOT NULL OR
            NEW.meta_schema_version IS NOT NULL OR NEW.meta_copy_version IS NOT NULL OR
            NEW.prediction_exposed OR NEW.model_issue_enabled OR NEW.assist_enabled OR
            NEW.prediction_artifact_id IS NOT NULL OR NEW.external_task_key <> '' OR
            NEW.dataset_source <> '' OR NEW.import_batch_key <> '' OR
            NEW.previous_round_task_id IS NOT NULL OR NEW.replacement_task_id IS NOT NULL OR
            NEW.source_media_import_preview_id IS NOT NULL OR
            NEW.published_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR
            NEW.tombstoned_at IS NULL OR TRIM(NEW.terminal_reason) = '' OR EXISTS (
                SELECT 1 FROM work_taskmediavariant WHERE task_id = OLD.task_id
            )
        ))
    )
    BEGIN
        SELECT RAISE(ABORT, 'Invalid draft task transition.');
    END
    """,
    """
    CREATE TRIGGER work_task_media_immutable_insert
    BEFORE INSERT ON work_taskmediavariant
    WHEN EXISTS (
        SELECT 1 FROM work_task
        WHERE task_id = NEW.task_id
          AND status <> 'draft'
    )
    BEGIN
        SELECT RAISE(ABORT, 'Published task media bindings are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_task_media_immutable_update
    BEFORE UPDATE ON work_taskmediavariant
    WHEN EXISTS (
        SELECT 1 FROM work_task
        WHERE task_id IN (OLD.task_id, NEW.task_id)
          AND status <> 'draft'
    )
    BEGIN
        SELECT RAISE(ABORT, 'Published task media bindings are immutable.');
    END
    """,
    """
    CREATE TRIGGER work_task_media_immutable_delete
    BEFORE DELETE ON work_taskmediavariant
    WHEN EXISTS (
        SELECT 1 FROM work_task
        WHERE task_id = OLD.task_id
          AND status <> 'draft'
    )
    BEGIN
        SELECT RAISE(ABORT, 'Published task media bindings are immutable.');
    END
    """,
)

POSTGRES_DROP = (
    "DROP TRIGGER IF EXISTS work_task_media_immutable ON work_taskmediavariant",
    "DROP FUNCTION IF EXISTS work_reject_task_media_mutation()",
    "DROP TRIGGER IF EXISTS work_task_contract_immutable ON work_task",
    "DROP FUNCTION IF EXISTS work_reject_task_contract_mutation()",
)

SQLITE_DROP = (
    "DROP TRIGGER IF EXISTS work_task_media_immutable_delete",
    "DROP TRIGGER IF EXISTS work_task_media_immutable_update",
    "DROP TRIGGER IF EXISTS work_task_media_immutable_insert",
    "DROP TRIGGER IF EXISTS work_task_draft_transition",
    "DROP TRIGGER IF EXISTS work_task_contract_immutable",
    "DROP TRIGGER IF EXISTS work_task_terminal_immutable",
    "DROP TRIGGER IF EXISTS work_task_id_immutable",
    "DROP TRIGGER IF EXISTS work_task_preserve_id",
)


def create_triggers(_apps: Any, schema_editor: Any) -> None:
    statements = {
        "postgresql": POSTGRES_CREATE,
        "sqlite": SQLITE_CREATE,
    }.get(schema_editor.connection.vendor, ())
    for statement in statements:
        schema_editor.execute(statement)


def drop_triggers(_apps: Any, schema_editor: Any) -> None:
    statements = {
        "postgresql": POSTGRES_DROP,
        "sqlite": SQLITE_DROP,
    }.get(schema_editor.connection.vendor, ())
    for statement in statements:
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("work", "0001_initial")]

    operations = [migrations.RunPython(create_triggers, drop_triggers)]
