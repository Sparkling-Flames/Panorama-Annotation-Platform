from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
from identity.models import User
from media.models import Asset, MediaImportPreview, MediaVariant
from work.models import Task, TaskMediaVariant
from work.services import (
    cancel_task,
    create_manual_annotation_round,
    create_task_draft,
    publish_task,
    supersede_task,
    tombstone_task,
)

pytestmark = pytest.mark.django_db


def media_contract(source_suffix: str = "main") -> tuple[Asset, tuple[MediaVariant, ...]]:
    asset = Asset.objects.create(source_key=f"panoramas/task-{source_suffix}")
    variants = tuple(
        MediaVariant.objects.create(
            asset=asset,
            source_key=f"cos://task-{source_suffix}/{role}",
            object_version=f"version-{role}",
            content_sha256=digest * 64,
            content_length=content_length,
            content_crc64ecma=crc64,
            width=width,
            height=height,
            format=format_code,
            role=role,
            coordinate_mapping=MediaVariant.CoordinateMapping.NORMALIZED_IDENTITY,
        )
        for role, digest, content_length, crc64, width, height, format_code in (
            (MediaVariant.Role.HIGH_RESOLUTION, "a", 200, "11", 32, 16, "png"),
            (MediaVariant.Role.COMPRESSED, "b", 100, "22", 16, 8, "jpeg"),
        )
    )
    for variant in variants:
        variant.publish()
    return asset, variants


def published_manual_task(source_suffix: str) -> Task:
    asset, variants = media_contract(source_suffix)
    draft = create_task_draft(asset=asset, media_variants=variants, mode=Task.Mode.MANUAL)
    return publish_task(task_id=draft.task_id)


def test_pap_tba_sc_001_published_manual_task_cannot_change_to_semi() -> None:
    task = published_manual_task("immutable")

    assert task.mode == Task.Mode.MANUAL

    task.mode = Task.Mode.SEMI
    with pytest.raises(ValidationError):
        task.save()

    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(mode=Task.Mode.SEMI)

    task.refresh_from_db()
    assert task.mode == Task.Mode.MANUAL


def test_pap_tba_sc_002_published_display_contract_and_media_are_database_immutable() -> None:
    task = published_manual_task("display-contract")
    _asset, extra_variants = media_contract("extra")

    assert task.prediction_exposed is False
    assert task.model_issue_enabled is False
    assert task.assist_enabled is False

    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(prediction_exposed=True)

    with pytest.raises(DatabaseError), transaction.atomic():
        TaskMediaVariant.objects.bulk_create(
            [TaskMediaVariant(task=task, media_variant=extra_variants[0])]
        )

    task.refresh_from_db()
    assert task.mode == Task.Mode.MANUAL
    assert task.allowed_media_variants.count() == 2


def test_task_id_is_database_immutable_from_draft_allocation() -> None:
    asset, variants = media_contract("stable-id")
    task = create_task_draft(asset=asset, media_variants=variants, mode=Task.Mode.MANUAL)
    allocated_id = task.task_id

    with transaction.atomic(), pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=allocated_id).update(task_id=uuid4())

    assert Task.objects.filter(pk=allocated_id).exists()


def test_task_id_is_model_immutable_after_persistence() -> None:
    asset, variants = media_contract("stable-model-id")
    task = create_task_draft(asset=asset, media_variants=variants, mode=Task.Mode.MANUAL)
    allocated_id = task.task_id

    task.task_id = uuid4()
    with pytest.raises(ValidationError):
        task.save()

    assert Task.objects.filter(pk=allocated_id).exists()


def test_published_task_cannot_be_reopened_before_contract_mutation() -> None:
    task = published_manual_task("two-step-published")

    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(
            status=Task.Status.DRAFT,
            published_at=None,
        )

    task.refresh_from_db()
    assert task.status == Task.Status.PUBLISHED
    assert task.published_at is not None


def test_tombstone_cannot_be_reopened_or_rehydrated() -> None:
    asset, variants = media_contract("sealed-tombstone")
    task = tombstone_task(
        task_id=create_task_draft(
            asset=asset,
            media_variants=variants,
            mode=Task.Mode.MANUAL,
        ).task_id,
        reason="mistaken draft",
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(status=Task.Status.DRAFT)
    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(asset=asset, mode=Task.Mode.MANUAL)

    task.refresh_from_db()
    assert task.status == Task.Status.TOMBSTONED
    assert task.asset_id is None


def test_cancelled_and_superseded_tasks_are_terminally_immutable() -> None:
    cancelled = cancel_task(
        task_id=published_manual_task("sealed-cancelled").task_id,
        reason="wrong contract",
    )
    superseded_source = published_manual_task("sealed-superseded")
    replacement = published_manual_task("sealed-replacement")
    superseded = supersede_task(
        task_id=superseded_source.task_id,
        replacement_task_id=replacement.task_id,
        reason="new contract",
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=cancelled.pk).update(status=Task.Status.PUBLISHED)
    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=superseded.pk).update(terminal_reason="rewritten")

    cancelled.refresh_from_db()
    superseded.refresh_from_db()
    assert cancelled.status == Task.Status.CANCELLED
    assert superseded.terminal_reason == "new contract"


def test_superseded_task_requires_a_different_published_replacement() -> None:
    task = published_manual_task("supersede-source")
    replacement_asset, replacement_variants = media_contract("draft-replacement")
    draft_replacement = create_task_draft(
        asset=replacement_asset,
        media_variants=replacement_variants,
        mode=Task.Mode.MANUAL,
    )

    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(
            status=Task.Status.SUPERSEDED,
            replacement_task_id=task.pk,
            terminal_reason="self replacement",
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(
            status=Task.Status.SUPERSEDED,
            replacement_task_id=draft_replacement.pk,
            terminal_reason="draft replacement",
        )

    task.status = Task.Status.SUPERSEDED
    task.replacement_task = task
    task.terminal_reason = "self replacement"
    with pytest.raises(ValidationError):
        task.save()

    task.refresh_from_db()
    assert task.status == Task.Status.PUBLISHED
    assert task.replacement_task_id is None


def test_published_task_media_bindings_cannot_be_updated_or_deleted() -> None:
    task = published_manual_task("sealed-media")
    binding = TaskMediaVariant.objects.filter(task=task).first()
    assert binding is not None

    with pytest.raises(DatabaseError), transaction.atomic():
        TaskMediaVariant.objects.filter(pk=binding.pk).update(created_at=timezone.now())
    with pytest.raises(DatabaseError), transaction.atomic():
        TaskMediaVariant.objects.filter(pk=binding.pk).delete()

    assert TaskMediaVariant.objects.filter(task=task).count() == 2


def test_semi_draft_has_an_isolated_display_policy_but_cannot_publish_without_prediction() -> None:
    asset, variants = media_contract("semi")

    task = create_task_draft(asset=asset, media_variants=variants, mode=Task.Mode.SEMI)

    assert task.prediction_exposed is True
    assert task.model_issue_enabled is True
    assert task.assist_enabled is False
    with pytest.raises(ValidationError) as rejected:
        publish_task(task_id=task.task_id)
    assert rejected.value.code == "semi_prediction_not_ready"


def test_pap_tba_sc_003_tombstone_releases_draft_contract_but_preserves_task_id() -> None:
    asset, variants = media_contract("tombstone")
    actor = User.objects.create_user(username="tombstone-admin", role=User.Role.ADMIN)
    source_preview = MediaImportPreview.objects.create(
        actor=actor,
        plan={"asset_source_key": asset.source_key},
        plan_sha256="c" * 64,
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    draft = create_task_draft(
        asset=asset,
        media_variants=variants,
        mode=Task.Mode.MANUAL,
        external_task_key="legacy-42",
        dataset_source="external-fixture",
        import_batch_key="import-1",
        source_media_import_preview=source_preview,
    )
    task_id = draft.task_id

    tombstone = tombstone_task(task_id=task_id, reason="mistaken draft")

    assert tombstone.task_id == task_id
    assert tombstone.status == Task.Status.TOMBSTONED
    assert tombstone.asset_id is None
    assert tombstone.mode is None
    assert tombstone.external_task_key == ""
    assert tombstone.source_media_import_preview_id is None
    assert tombstone.allowed_media_variants.count() == 0
    with pytest.raises(ValidationError):
        tombstone.delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.bulk_create([Task(task_id=task_id, status=Task.Status.TOMBSTONED)])


def test_pap_tba_sc_004_published_tasks_can_be_cancelled_or_superseded_without_rewrite() -> None:
    cancelled = cancel_task(
        task_id=published_manual_task("cancelled").task_id,
        reason="wrong contract",
    )
    original = published_manual_task("original")
    replacement = published_manual_task("replacement")

    superseded = supersede_task(
        task_id=original.task_id,
        replacement_task_id=replacement.task_id,
        reason="new annotation contract",
    )

    assert cancelled.status == Task.Status.CANCELLED
    assert cancelled.terminal_reason == "wrong contract"
    assert cancelled.cancelled_at is not None
    assert superseded.status == Task.Status.SUPERSEDED
    assert superseded.replacement_task_id == replacement.task_id
    assert superseded.terminal_reason == "new annotation contract"
    assert superseded.asset_id == original.asset_id


def test_pap_tba_sc_005_external_key_is_not_the_platform_task_id() -> None:
    asset, variants = media_contract("external-key")

    task = create_task_draft(
        asset=asset,
        media_variants=variants,
        mode=Task.Mode.MANUAL,
        external_task_key="base_task_id=17",
        dataset_source="MP3D",
        import_batch_key="legacy-import-2026-07",
    )

    assert isinstance(task.task_id, UUID)
    assert str(task.task_id) != task.external_task_key
    assert task.external_task_key == "base_task_id=17"
    assert task.dataset_source == "MP3D"
    assert task.import_batch_key == "legacy-import-2026-07"


def test_new_round_reuses_asset_but_creates_a_new_idempotent_task_contract() -> None:
    asset, variants = media_contract("rounds")
    actor = User.objects.create_user(username="round-admin", role=User.Role.ADMIN)

    def stored_preview(suffix: str) -> MediaImportPreview:
        return MediaImportPreview.objects.create(
            actor=actor,
            plan={"round": suffix},
            plan_sha256=suffix * 64,
            expires_at=timezone.now() + timedelta(minutes=15),
        )

    first_preview = stored_preview("a")
    second_preview = stored_preview("b")

    first_round = create_manual_annotation_round(
        source_preview_id=first_preview.preview_id,
        asset=asset,
        media_variants=variants,
    )
    retry = create_manual_annotation_round(
        source_preview_id=first_preview.preview_id,
        asset=asset,
        media_variants=variants,
    )
    second_round = create_manual_annotation_round(
        source_preview_id=second_preview.preview_id,
        asset=asset,
        media_variants=variants,
    )

    assert retry.task_id == first_round.task_id
    assert second_round.task_id != first_round.task_id
    assert second_round.asset_id == first_round.asset_id == asset.asset_id
    assert second_round.previous_round_task_id == first_round.task_id
    assert first_round.status == second_round.status == Task.Status.PUBLISHED
