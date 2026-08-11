from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.utils import timezone
from identity.models import ActiveWorkspace, DataNoticeAcceptance, User
from identity.services import CURRENT_DATA_NOTICE_VERSION
from media.models import Asset, MediaVariant
from work.annotation_state import annotation_state_sha
from work.models import (
    AdjudicatedRevision,
    AnnotationRevision,
    Assignment,
    CurrentDraft,
    DraftCycle,
    ReviewRecord,
    Task,
    TaskDeliverySelection,
)
from work.services import (
    assign_task,
    create_task_draft,
    create_work_batch,
    open_owned_assignment,
    publish_task,
    save_owned_current_draft,
    submit_owned_current_draft,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL trigger integration test",
    ),
]


def published_assignment(username: str) -> tuple[User, Assignment]:
    worker = User.objects.create_user(
        username=username,
        password=None,
        role=User.Role.WORKER,
        must_change_password=False,
    )
    DataNoticeAcceptance.objects.create(worker=worker, notice_version=CURRENT_DATA_NOTICE_VERSION)
    asset = Asset.objects.create(source_key=f"panoramas/{username}")
    variant = MediaVariant.objects.create(
        asset=asset,
        source_key=f"cos://{username}/compressed",
        object_version=f"version-{username}",
        content_sha256="a" * 64,
        content_length=100,
        content_crc64ecma="123",
        width=16,
        height=8,
        format="jpeg",
        role=MediaVariant.Role.COMPRESSED,
        coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
    )
    variant.publish()
    task = publish_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=[variant],
            mode=Task.Mode.MANUAL,
        ).task_id
    )
    assignment = assign_task(
        batch=create_work_batch(name=f"Batch {username}"),
        task=task,
        worker=worker,
    )
    return worker, assignment


def submission_state() -> dict[str, object]:
    return {
        "difficulty": ["trivial"],
        "geometry_attempt_reason_text": "Image evidence is insufficient.",
        "geometry_attempt_status": "not_drawable",
        "pairs": [],
        "portals": [],
        "seam_anchor_pair_id": None,
        "scope_reason_codes": ["insufficient_evidence"],
        "scope_reason_text": "",
        "worker_scope_observation": "representation_oos",
    }


def state_sha_for(assignment: Assignment, state: object) -> str:
    return annotation_state_sha(
        state,
        meta_schema_version=assignment.task.meta_schema_version or "",
        task_mode=assignment.task.mode or "",
    )


@pytest.fixture
def revision() -> AnnotationRevision:
    worker, assignment = published_assignment("immutable-revision-worker")
    cycle = DraftCycle.objects.create(assignment=assignment, cycle_no=1)
    state = submission_state()
    CurrentDraft.objects.create(
        draft_cycle=cycle,
        state=state,
        state_sha=state_sha_for(assignment, state),
    )
    return AnnotationRevision.objects.create(
        assignment=assignment,
        task=assignment.task,
        worker=worker,
        draft_cycle=cycle,
        revision_no=1,
        state=state,
        state_sha=state_sha_for(assignment, state),
        source_draft_version=0,
        meta_schema_version=assignment.task.meta_schema_version,
        meta_copy_version=assignment.task.meta_copy_version,
        submission_locale="zh-CN",
        idempotency_key=uuid4(),
    )


def test_concurrent_same_key_submission_creates_one_revision() -> None:
    worker, assignment = published_assignment("concurrent-revision-worker")
    tab_id = uuid4()
    workspace = ActiveWorkspace.objects.create(
        worker=worker,
        session_key="concurrent-session",
        client_instance_id=uuid4(),
        tab_id=tab_id,
        lease_expires_at=timezone.now() + timedelta(minutes=5),
    )
    workspace_args = {
        "session_key": workspace.session_key,
        "session_token": str(workspace.token),
        "tab_id": tab_id,
    }
    open_owned_assignment(
        actor=worker,
        assignment_id=assignment.assignment_id,
        **workspace_args,
    )
    draft = save_owned_current_draft(
        actor=worker,
        assignment_id=assignment.assignment_id,
        expected_draft_version=0,
        state=submission_state(),
        **workspace_args,
    )
    idempotency_key = uuid4()
    barrier = Barrier(2)

    def submit() -> tuple[str, bool]:
        close_old_connections()
        try:
            actor = User.objects.get(pk=worker.pk)
            barrier.wait(timeout=10)
            result, created = submit_owned_current_draft(
                actor=actor,
                assignment_id=assignment.assignment_id,
                expected_state_sha=draft.state_sha,
                idempotency_key=idempotency_key,
                locale="zh-CN",
                client_build_sha="test-client-build",
                viewer_version="annotation-viewer-v1",
                interaction_contract_version="annotation-interaction-v1",
                **workspace_args,
            )
            return str(result.revision_id), created
        except ValidationError as error:
            return error.code or "validation_error", False
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))

    assert results[0][0] == results[1][0]
    assert {created for _, created in results} == {False, True}
    assert AnnotationRevision.objects.count() == 1
    assignment.refresh_from_db()
    assert assignment.work_state == Assignment.WorkState.SUBMITTED
    assert DraftCycle.objects.get(assignment=assignment).closed_at is not None


def test_postgresql_rejects_revision_queryset_update(revision: AnnotationRevision) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        AnnotationRevision.objects.filter(pk=revision.pk).update(state_sha="f" * 64)


def test_postgresql_rejects_revision_queryset_delete(revision: AnnotationRevision) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        AnnotationRevision.objects.filter(pk=revision.pk).delete()


def test_postgresql_rejects_revision_raw_sql_update(revision: AnnotationRevision) -> None:
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE work_annotationrevision SET state_sha = %s WHERE revision_id = %s",
            ["f" * 64, revision.pk],
        )


def test_postgresql_rejects_revision_raw_sql_delete(revision: AnnotationRevision) -> None:
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM work_annotationrevision WHERE revision_id = %s",
            [revision.pk],
        )


def test_postgresql_rejects_review_adjudication_and_selection_mutation(
    revision: AnnotationRevision,
) -> None:
    administrator = User.objects.create_user(
        username="immutable-review-admin",
        password=None,
        role=User.Role.ADMIN,
        must_change_password=False,
    )
    review = ReviewRecord.objects.create(
        revision=revision,
        reviewer=administrator,
        outcome=ReviewRecord.Outcome.ACCEPTED,
        reason="",
        rule_version="revision-review-v1",
    )
    adjudication = AdjudicatedRevision.objects.create(
        task=revision.task,
        actor=administrator,
        state=revision.state,
        state_sha=revision.state_sha,
        source_revision_ids=[str(revision.revision_id)],
        meta_schema_version=revision.meta_schema_version,
        meta_copy_version=revision.meta_copy_version,
        reason="Resolve the source revision.",
        rule_version="adjudication-v1",
    )
    selection = TaskDeliverySelection.objects.create(
        task=revision.task,
        actor=administrator,
        adjudicated_revision=adjudication,
        reason="Use the adjudicated result.",
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        ReviewRecord.objects.filter(pk=review.pk).update(reason="rewritten")
    with pytest.raises(DatabaseError), transaction.atomic():
        AdjudicatedRevision.objects.filter(pk=adjudication.pk).delete()
    with pytest.raises(DatabaseError), transaction.atomic():
        TaskDeliverySelection.objects.filter(pk=selection.pk).update(reason="rewritten")
