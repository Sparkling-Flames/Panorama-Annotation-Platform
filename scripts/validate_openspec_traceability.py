from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHANGE_NAME = "build-panorama-annotation-platform-v1"
SPEC_ROOT = PROJECT_ROOT / "openspec" / "changes" / CHANGE_NAME / "specs"
DEFAULT_MANIFEST = PROJECT_ROOT / "tests" / "traceability" / "openspec_scenarios.json"
REQUIREMENT_PATTERN = re.compile(r"^### Requirement: (.+)$")
SCENARIO_PATTERN = re.compile(r"^#### Scenario: (.+)$")

CAPABILITY_METADATA = {
    "activity-offline": ("AOF", ["8"]),
    "adaptive-consensus-routing": ("ACR", ["10", "11"]),
    "admin-audit-export": ("AAE", ["12"]),
    "annotation-contract": ("ANN", ["5"]),
    "draft-revision-review": ("DRR", ["6"]),
    "identity-access": ("IAM", ["2"]),
    "media-ingestion-delivery": ("MID", ["3"]),
    "platform-boundaries": ("PBD", ["13"]),
    "prediction-assist": ("PAS", ["9"]),
    "preview-validation": ("PRV", ["7"]),
    "task-batch-assignment": ("TBA", ["4", "11"]),
}


@dataclass(frozen=True)
class RequirementSpec:
    title: str
    scenarios: tuple[str, ...]


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    requirements: tuple[RequirementSpec, ...]


def parse_capability_spec(spec_path: Path) -> CapabilitySpec:
    requirements: list[RequirementSpec] = []
    current_title: str | None = None
    current_scenarios: list[str] = []

    for line in spec_path.read_text(encoding="utf-8").splitlines():
        requirement_match = REQUIREMENT_PATTERN.match(line)
        if requirement_match:
            if current_title is not None:
                requirements.append(RequirementSpec(current_title, tuple(current_scenarios)))
            current_title = requirement_match.group(1).strip()
            current_scenarios = []
            continue

        scenario_match = SCENARIO_PATTERN.match(line)
        if scenario_match:
            if current_title is None:
                raise ValueError(f"Scenario appears before a requirement in {spec_path}")
            current_scenarios.append(scenario_match.group(1).strip())

    if current_title is not None:
        requirements.append(RequirementSpec(current_title, tuple(current_scenarios)))

    if not requirements:
        raise ValueError(f"No requirements found in {spec_path}")
    if any(not requirement.scenarios for requirement in requirements):
        raise ValueError(f"Every requirement must contain a scenario in {spec_path}")

    return CapabilitySpec(spec_path.parent.name, tuple(requirements))


def current_specs() -> tuple[CapabilitySpec, ...]:
    spec_paths = sorted(SPEC_ROOT.glob("*/spec.md"))
    capabilities = tuple(parse_capability_spec(path) for path in spec_paths)
    actual_names = {capability.name for capability in capabilities}
    expected_names = set(CAPABILITY_METADATA)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise ValueError(f"Capability metadata drift: missing={missing}, extra={extra}")
    return capabilities


def template_manifest(specs: tuple[CapabilitySpec, ...]) -> dict[str, Any]:
    capabilities: list[dict[str, Any]] = []
    for capability in specs:
        prefix, task_sections = CAPABILITY_METADATA[capability.name]
        scenario_number = 0
        requirements: list[dict[str, Any]] = []
        for requirement_number, requirement in enumerate(capability.requirements, start=1):
            scenarios: list[dict[str, Any]] = []
            for scenario in requirement.scenarios:
                scenario_number += 1
                scenarios.append(
                    {
                        "title": scenario,
                        "test_ids": [f"PAP-{prefix}-SC-{scenario_number:03d}"],
                        "coverage_status": "planned",
                        "test_files": [],
                    }
                )
            requirements.append(
                {
                    "requirement_id": f"PAP-{prefix}-REQ-{requirement_number:03d}",
                    "title": requirement.title,
                    "scenarios": scenarios,
                }
            )
        capabilities.append(
            {
                "capability": capability.name,
                "prefix": prefix,
                "implementation_task_sections": task_sections,
                "requirements": requirements,
            }
        )

    return {
        "schema_version": 1,
        "change": CHANGE_NAME,
        "id_convention": "PAP-<CAPABILITY_PREFIX>-SC-<NNN>",
        "capabilities": capabilities,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"manifest does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"manifest is not valid JSON: {error}") from error
    if not isinstance(loaded, dict):
        raise ValueError("manifest root must be an object")
    return loaded


def object_list(value: Any, field: str, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        errors.append(f"{field} must be a list of objects")
        return []
    return value


def is_collected_test_path(path: Path) -> bool:
    normalized = path.as_posix()
    return bool(
        re.fullmatch(r"backend/tests/(?:.*/)?test_[^/]+\.py", normalized)
        or re.fullmatch(r"frontend/src/.+\.test\.tsx?", normalized)
        or re.fullmatch(r"e2e/tests/.+\.spec\.ts", normalized)
    )


def contains_test_id(source: str, test_id: str) -> bool:
    normalized_source = source.lower()
    return (
        test_id.lower() in normalized_source
        or test_id.lower().replace("-", "_") in normalized_source
    )


def validate_manifest(manifest: dict[str, Any], specs: tuple[CapabilitySpec, ...]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if manifest.get("change") != CHANGE_NAME:
        errors.append(f"change must be {CHANGE_NAME}")

    capability_entries = object_list(manifest.get("capabilities"), "capabilities", errors)
    capability_by_name: dict[str, dict[str, Any]] = {}
    for capability_entry in capability_entries:
        capability_name = capability_entry.get("capability")
        if isinstance(capability_name, str):
            capability_by_name[capability_name] = capability_entry
    expected_names = {spec.name for spec in specs}
    actual_names = set(capability_by_name)
    for name in sorted(expected_names - actual_names):
        errors.append(f"missing capability mapping: {name}")
    for name in sorted(actual_names - expected_names):
        errors.append(f"unexpected capability mapping: {name}")

    seen_requirement_ids: set[str] = set()
    seen_test_ids: set[str] = set()

    for capability in specs:
        entry = capability_by_name.get(capability.name)
        if entry is None:
            continue
        prefix, expected_task_sections = CAPABILITY_METADATA[capability.name]
        if entry.get("prefix") != prefix:
            errors.append(f"{capability.name}: prefix must be {prefix}")
        if entry.get("implementation_task_sections") != expected_task_sections:
            errors.append(
                f"{capability.name}: implementation_task_sections must be {expected_task_sections}"
            )

        requirement_entries = object_list(
            entry.get("requirements"), f"{capability.name}.requirements", errors
        )
        requirement_by_title: dict[str, dict[str, Any]] = {}
        for mapped_requirement in requirement_entries:
            requirement_title = mapped_requirement.get("title")
            if isinstance(requirement_title, str):
                requirement_by_title[requirement_title] = mapped_requirement
        expected_requirement_titles = {requirement.title for requirement in capability.requirements}
        actual_requirement_titles = set(requirement_by_title)
        for title in sorted(expected_requirement_titles - actual_requirement_titles):
            errors.append(f"{capability.name}: missing requirement mapping: {title}")
        for title in sorted(actual_requirement_titles - expected_requirement_titles):
            errors.append(f"{capability.name}: unexpected requirement mapping: {title}")

        for requirement in capability.requirements:
            requirement_entry = requirement_by_title.get(requirement.title)
            if requirement_entry is None:
                continue
            requirement_id = requirement_entry.get("requirement_id")
            if not isinstance(requirement_id, str) or not re.fullmatch(
                rf"PAP-{prefix}-REQ-\d{{3}}", requirement_id
            ):
                errors.append(f"{capability.name}/{requirement.title}: invalid requirement_id")
            elif requirement_id in seen_requirement_ids:
                errors.append(f"duplicate requirement_id: {requirement_id}")
            else:
                seen_requirement_ids.add(requirement_id)

            scenario_entries = object_list(
                requirement_entry.get("scenarios"),
                f"{capability.name}/{requirement.title}.scenarios",
                errors,
            )
            scenario_by_title: dict[str, dict[str, Any]] = {}
            for mapped_scenario in scenario_entries:
                scenario_title = mapped_scenario.get("title")
                if isinstance(scenario_title, str):
                    scenario_by_title[scenario_title] = mapped_scenario
            expected_scenarios = set(requirement.scenarios)
            actual_scenarios = set(scenario_by_title)
            for title in sorted(expected_scenarios - actual_scenarios):
                errors.append(
                    f"{capability.name}/{requirement.title}: missing scenario mapping: {title}"
                )
            for title in sorted(actual_scenarios - expected_scenarios):
                errors.append(
                    f"{capability.name}/{requirement.title}: unexpected scenario mapping: {title}"
                )

            for scenario_title in requirement.scenarios:
                scenario_entry = scenario_by_title.get(scenario_title)
                if scenario_entry is None:
                    continue
                test_ids = scenario_entry.get("test_ids")
                valid_test_ids: list[str] = []
                if not isinstance(test_ids, list) or not test_ids:
                    errors.append(f"{capability.name}/{scenario_title}: test_ids must not be empty")
                    continue
                for test_id in test_ids:
                    if not isinstance(test_id, str) or not re.fullmatch(
                        rf"PAP-{prefix}-SC-\d{{3}}", test_id
                    ):
                        errors.append(
                            f"{capability.name}/{scenario_title}: invalid test_id {test_id!r}"
                        )
                    elif test_id in seen_test_ids:
                        errors.append(f"duplicate test_id: {test_id}")
                    else:
                        seen_test_ids.add(test_id)
                        valid_test_ids.append(test_id)

                coverage_status = scenario_entry.get("coverage_status")
                if coverage_status not in {"planned", "implemented"}:
                    errors.append(f"{capability.name}/{scenario_title}: invalid coverage_status")
                test_files = scenario_entry.get("test_files")
                if not isinstance(test_files, list) or any(
                    not isinstance(path, str) for path in test_files
                ):
                    errors.append(
                        f"{capability.name}/{scenario_title}: test_files must be a string list"
                    )
                elif coverage_status == "implemented" and not test_files:
                    errors.append(
                        f"{capability.name}/{scenario_title}: implemented coverage requires test_files"
                    )
                elif coverage_status == "implemented":
                    sources: list[str] = []
                    for test_file in test_files:
                        resolved_path = (PROJECT_ROOT / test_file).resolve()
                        try:
                            relative_path = resolved_path.relative_to(PROJECT_ROOT.resolve())
                        except ValueError:
                            errors.append(
                                f"{capability.name}/{scenario_title}: test file escapes project root: {test_file}"
                            )
                            continue
                        if not is_collected_test_path(relative_path):
                            errors.append(
                                f"{capability.name}/{scenario_title}: test file is not a collected test path: {test_file}"
                            )
                            continue
                        if not resolved_path.is_file():
                            errors.append(
                                f"{capability.name}/{scenario_title}: test file does not exist: {test_file}"
                            )
                            continue
                        try:
                            sources.append(resolved_path.read_text(encoding="utf-8"))
                        except (OSError, UnicodeError):
                            errors.append(
                                f"{capability.name}/{scenario_title}: test file is not readable UTF-8: {test_file}"
                            )
                    for test_id in valid_test_ids:
                        if not any(contains_test_id(source, test_id) for source in sources):
                            errors.append(
                                f"{capability.name}/{scenario_title}: test evidence does not contain test ID {test_id}"
                            )

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="校验 OpenSpec requirement/scenario 到测试 ID 的完整映射。"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--write-template",
        action="store_true",
        help="仅在 manifest 不存在时生成初始 planned 覆盖清单。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = PROJECT_ROOT / manifest_path

    try:
        specs = current_specs()
        if args.write_template:
            if manifest_path.exists():
                raise ValueError(f"refusing to overwrite existing manifest: {manifest_path}")
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(template_manifest(specs), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"Wrote traceability template: {manifest_path.relative_to(PROJECT_ROOT)}")

        manifest = load_manifest(manifest_path)
        errors = validate_manifest(manifest, specs)
    except ValueError as validation_error:
        print(f"Traceability validation failed: {validation_error}")
        return 1

    if errors:
        for issue in errors:
            print(issue)
        print(f"Traceability validation failed with {len(errors)} issue(s).")
        return 1

    requirement_count = sum(len(capability.requirements) for capability in specs)
    scenario_count = sum(
        len(requirement.scenarios)
        for capability in specs
        for requirement in capability.requirements
    )
    print(
        "Traceability validation passed: "
        f"{len(specs)} capabilities, {requirement_count} requirements, "
        f"{scenario_count} scenarios."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
