from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection

SECURITY_MIGRATION = import_module("work.migrations.0028_harden_postgres_security")
API_ROLES = ("anon", "authenticated", "service_role")

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL privilege and function security integration test",
    ),
]


def test_trigger_functions_have_a_fixed_search_path() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT proname, proconfig
            FROM pg_proc
            WHERE pronamespace = 'public'::regnamespace
              AND proname = ANY(%s)
            ORDER BY proname
            """,
            [list(SECURITY_MIGRATION.TRIGGER_FUNCTION_NAMES)],
        )
        configurations = dict(cursor.fetchall())

    assert set(configurations) == set(SECURITY_MIGRATION.TRIGGER_FUNCTION_NAMES)
    assert all(
        config is not None and "search_path=pg_catalog, public" in config
        for config in configurations.values()
    )


def test_supabase_api_roles_have_no_effective_public_object_privileges() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
            [list(API_ROLES)],
        )
        present_roles = {row[0] for row in cursor.fetchall()}
        if present_roles != set(API_ROLES):
            pytest.skip("Supabase API role fixtures are not installed")

        cursor.execute(
            """
            SELECT api_role, relation.relname
            FROM unnest(%s::text[]) AS api_role
            CROSS JOIN pg_class AS relation
            WHERE relation.relnamespace = 'public'::regnamespace
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND has_table_privilege(
                  api_role,
                  relation.oid,
                  'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
              )
            """,
            [list(API_ROLES)],
        )
        table_privileges = cursor.fetchall()

        cursor.execute(
            """
            SELECT api_role, sequence.relname
            FROM unnest(%s::text[]) AS api_role
            CROSS JOIN pg_class AS sequence
            WHERE sequence.relnamespace = 'public'::regnamespace
              AND sequence.relkind = 'S'
              AND has_sequence_privilege(api_role, sequence.oid, 'USAGE, SELECT, UPDATE')
            """,
            [list(API_ROLES)],
        )
        sequence_privileges = cursor.fetchall()

        cursor.execute(
            """
            SELECT api_role, routine.proname
            FROM unnest(%s::text[]) AS api_role
            CROSS JOIN pg_proc AS routine
            WHERE routine.pronamespace = 'public'::regnamespace
              AND has_function_privilege(api_role, routine.oid, 'EXECUTE')
            """,
            [list(API_ROLES)],
        )
        function_privileges = cursor.fetchall()

    assert table_privileges == []
    assert sequence_privileges == []
    assert function_privileges == []


def test_public_function_execute_and_future_api_grants_are_revoked() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT routine.proname
            FROM pg_proc AS routine
            CROSS JOIN LATERAL aclexplode(
                COALESCE(routine.proacl, acldefault('f', routine.proowner))
            ) AS privilege
            WHERE routine.pronamespace = 'public'::regnamespace
              AND privilege.grantee = 0
              AND privilege.privilege_type = 'EXECUTE'
            """
        )
        public_functions = cursor.fetchall()

        cursor.execute(
            """
            SELECT defaults.defaclobjtype, COALESCE(role.rolname, 'PUBLIC')
            FROM pg_default_acl AS defaults
            CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS privilege
            LEFT JOIN pg_roles AS role ON role.oid = privilege.grantee
            WHERE defaults.defaclrole = (
                SELECT oid FROM pg_roles WHERE rolname = CURRENT_USER
            )
              AND defaults.defaclnamespace = 'public'::regnamespace
              AND (
                  privilege.grantee = 0
                  OR role.rolname = ANY(%s)
              )
            """,
            [list(API_ROLES)],
        )
        unsafe_defaults = cursor.fetchall()

    assert public_functions == []
    assert unsafe_defaults == []
