from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4


class AssistContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


@dataclass(frozen=True)
class AssistArtifact:
    assignment_id: UUID
    base_revision_id: UUID | None
    input_state_sha: str
    engine_version: str
    candidate_id: UUID
    candidate_state_sha: str

    def __post_init__(self) -> None:
        if (
            not _valid_sha256(self.input_state_sha)
            or not _valid_sha256(self.candidate_state_sha)
            or not self.engine_version.strip()
        ):
            raise AssistContractError("assist_artifact_invalid")


@dataclass(frozen=True)
class AssistEvent:
    candidate_id: UUID
    action: str
    state_sha_before: str
    state_sha_after: str
    event_id: UUID = field(default_factory=uuid4)


def _require_current(candidate: AssistArtifact, current_state_sha: str) -> None:
    if current_state_sha != candidate.input_state_sha:
        raise AssistContractError("assist_candidate_stale")


def apply_assist_candidate(
    candidate: AssistArtifact,
    *,
    current_state_sha: str,
) -> AssistEvent:
    _require_current(candidate, current_state_sha)
    return AssistEvent(
        candidate_id=candidate.candidate_id,
        action="apply",
        state_sha_before=candidate.input_state_sha,
        state_sha_after=candidate.candidate_state_sha,
    )


def ignore_assist_candidate(
    candidate: AssistArtifact,
    *,
    current_state_sha: str,
) -> AssistEvent:
    _require_current(candidate, current_state_sha)
    return AssistEvent(
        candidate_id=candidate.candidate_id,
        action="ignore",
        state_sha_before=candidate.input_state_sha,
        state_sha_after=candidate.input_state_sha,
    )


def undo_assist_apply(
    candidate: AssistArtifact,
    applied: AssistEvent,
    *,
    current_state_sha: str,
) -> AssistEvent:
    if (
        applied.action != "apply"
        or applied.candidate_id != candidate.candidate_id
        or applied.state_sha_before != candidate.input_state_sha
        or applied.state_sha_after != candidate.candidate_state_sha
    ):
        raise AssistContractError("assist_undo_invalid")
    if current_state_sha != candidate.candidate_state_sha:
        raise AssistContractError("assist_candidate_stale")
    return AssistEvent(
        candidate_id=candidate.candidate_id,
        action="undo_apply",
        state_sha_before=candidate.candidate_state_sha,
        state_sha_after=candidate.input_state_sha,
    )
