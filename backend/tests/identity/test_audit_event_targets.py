from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction
from identity.models import AuditEvent, User
from identity.services import record_account_audit, record_audit_event

pytestmark = pytest.mark.django_db


def test_generic_audit_event_records_real_target_reason_and_correlation() -> None:
    administrator = User.objects.create_user(username="audit-target-admin", role=User.Role.ADMIN)
    correlation_id = uuid4()
    batch_id = uuid4()

    event = record_audit_event(
        actor=administrator,
        action="batch.frozen",
        correlation_id=correlation_id,
        reason="administrator_request",
        target_id=batch_id,
        target_type="work_batch",
    )

    assert event.target_worker is None
    assert event.target_type == "work_batch"
    assert event.target_id == str(batch_id)
    assert event.reason == "administrator_request"
    assert event.correlation_id == correlation_id
    assert event.details == {}
    assert AuditEvent.objects.get(event_id=event.event_id) == event


def test_account_audit_uses_worker_as_both_domain_and_legacy_target() -> None:
    administrator = User.objects.create_user(username="audit-account-admin", role=User.Role.ADMIN)
    worker = User.objects.create_user(username="audit-account-worker", role=User.Role.WORKER)

    event = record_account_audit(
        actor=administrator,
        target_worker=worker,
        action="worker.disabled",
    )

    assert event.target_worker == worker
    assert event.target_type == "worker"
    assert event.target_id == str(worker.worker_id)
    assert event.correlation_id is not None


def test_generic_audit_event_requires_a_nonempty_reason_at_service_and_database_boundaries() -> (
    None
):
    administrator = User.objects.create_user(username="audit-reason-admin", role=User.Role.ADMIN)

    with pytest.raises(ValueError, match="reason"):
        record_audit_event(
            actor=administrator,
            action="batch.frozen",
            reason="   ",
            target_id=uuid4(),
            target_type="work_batch",
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent.objects.create(
            actor=administrator,
            action="batch.frozen",
            reason="",
            target_id=str(uuid4()),
            target_type="work_batch",
        )
