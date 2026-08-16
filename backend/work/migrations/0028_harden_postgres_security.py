from __future__ import annotations

from typing import Any

from django.db import migrations

TRIGGER_FUNCTION_NAMES = (
    "activity_reject_event_mutation",
    "identity_reject_audit_event_mutation",
    "media_reject_published_variant_mutation",
    "media_reject_registration_mutation",
    "work_reject_analysis_job_input_mutation",
    "work_reject_assignment_identity_mutation",
    "work_reject_audit_artifact_mutation",
    "work_reject_batch_export_mutation",
    "work_reject_consensus_artifact_mutation",
    "work_reject_eligibility_artifact_mutation",
    "work_reject_frozen_consensus_policy_mutation",
    "work_reject_frozen_scope_policy_mutation",
    "work_reject_metric_snapshot_mutation",
    "work_reject_prediction_artifact_mutation",
    "work_reject_published_task_rule_mutation",
    "work_reject_review_artifact_mutation",
    "work_reject_revision_mutation",
    "work_reject_submission_assessment_mutation",
    "work_reject_task_contract_mutation",
    "work_reject_task_media_mutation",
    "work_validate_task_prediction_contract",
)

REVOKE_DATA_API_PRIVILEGES = """
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC;

DO $panorama_security$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role']
    LOOP
        IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles
            WHERE rolname = api_role
        ) THEN
            EXECUTE pg_catalog.format(
                'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %%I',
                api_role
            );
            EXECUTE pg_catalog.format(
                'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %%I',
                api_role
            );
            EXECUTE pg_catalog.format(
                'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %%I',
                api_role
            );
            EXECUTE pg_catalog.format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
                'REVOKE ALL PRIVILEGES ON TABLES FROM %%I',
                api_role
            );
            EXECUTE pg_catalog.format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
                'REVOKE ALL PRIVILEGES ON SEQUENCES FROM %%I',
                api_role
            );
            EXECUTE pg_catalog.format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
                'REVOKE ALL PRIVILEGES ON FUNCTIONS FROM %%I',
                api_role
            );
        END IF;
    END LOOP;
END;
$panorama_security$;
"""


def harden_postgres_security(_apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return

    for function_name in TRIGGER_FUNCTION_NAMES:
        schema_editor.execute(
            f"ALTER FUNCTION public.{function_name}() SET search_path = pg_catalog, public"
        )
    schema_editor.execute(REVOKE_DATA_API_PRIVILEGES)


class Migration(migrations.Migration):
    dependencies = [
        ("activity", "0003_client_wall_time"),
        ("identity", "0005_data_notice_acceptance"),
        ("media", "0006_mediaimportpreview_publication_variant_ids"),
        ("work", "0027_platform_release_id"),
    ]

    operations = [
        migrations.RunPython(
            harden_postgres_security,
            reverse_code=migrations.RunPython.noop,
        )
    ]
