from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from work.assist import (
    AssistArtifact,
    AssistContractError,
    apply_assist_candidate,
    ignore_assist_candidate,
    undo_assist_apply,
)


def artifact() -> AssistArtifact:
    return AssistArtifact(
        assignment_id=uuid4(),
        base_revision_id=None,
        candidate_id=uuid4(),
        candidate_state_sha="b" * 64,
        engine_version="registered-engine-v1",
        input_state_sha="a" * 64,
    )


def test_pap_pas_sc_008_stale_candidate_cannot_apply_to_a_changed_state() -> None:
    candidate = artifact()

    with pytest.raises(AssistContractError) as stale:
        apply_assist_candidate(candidate, current_state_sha="c" * 64)

    assert stale.value.code == "assist_candidate_stale"


def test_task_9_6_single_candidate_apply_ignore_and_undo_are_immutable_events() -> None:
    candidate = artifact()

    applied = apply_assist_candidate(candidate, current_state_sha=candidate.input_state_sha)
    ignored = ignore_assist_candidate(candidate, current_state_sha=candidate.input_state_sha)
    undone = undo_assist_apply(candidate, applied, current_state_sha=candidate.candidate_state_sha)

    assert (applied.action, applied.state_sha_before, applied.state_sha_after) == (
        "apply",
        candidate.input_state_sha,
        candidate.candidate_state_sha,
    )
    assert (ignored.action, ignored.state_sha_before, ignored.state_sha_after) == (
        "ignore",
        candidate.input_state_sha,
        candidate.input_state_sha,
    )
    assert (undone.action, undone.state_sha_before, undone.state_sha_after) == (
        "undo_apply",
        candidate.candidate_state_sha,
        candidate.input_state_sha,
    )
    assert applied.candidate_id == ignored.candidate_id == undone.candidate_id
    with pytest.raises(FrozenInstanceError):
        applied.action = "ignore"  # type: ignore[misc]
