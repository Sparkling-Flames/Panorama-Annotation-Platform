from __future__ import annotations

import pytest
from django.db import DatabaseError, connection, transaction
from identity.models import AuditEvent, User

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL trigger integration test",
    ),
]


@pytest.fixture
def audit_event() -> AuditEvent:
    actor = User.objects.create_user(username="audit-actor", password=None, role=User.Role.ADMIN)
    worker = User.objects.create_user(username="audit-target", password=None)
    return AuditEvent.objects.create(
        actor=actor,
        target_worker=worker,
        target_type="worker",
        target_id=str(worker.worker_id),
        action="worker.created",
        reason="test",
        details={"reason": "test"},
    )


def test_postgresql_rejects_queryset_update(audit_event: AuditEvent) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=audit_event.pk).update(action="tampered")


def test_postgresql_rejects_queryset_delete(audit_event: AuditEvent) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=audit_event.pk).delete()


def test_postgresql_rejects_raw_sql_update(audit_event: AuditEvent) -> None:
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE identity_auditevent SET action = %s WHERE event_id = %s",
            ["tampered", audit_event.pk],
        )


def test_postgresql_rejects_raw_sql_delete(audit_event: AuditEvent) -> None:
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM identity_auditevent WHERE event_id = %s",
            [audit_event.pk],
        )
