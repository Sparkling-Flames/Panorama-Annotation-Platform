"""Server canonicalization uses finite IEEE-754 binary64 coordinates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from math import isfinite
from typing import NotRequired, TypedDict
from uuid import UUID

from .meta_schema import (
    DIFFICULTY_CODES,
    META_SCHEMA_V1,
    MODEL_ISSUE_CODES,
    POC_META_SCHEMA_VERSION,
    SCOPE_OBSERVATIONS,
    SCOPE_REASON_CODES,
)


class PointPayload(TypedDict):
    point_id: str
    u: float
    v: float


class PointPairPayload(TypedDict):
    pair_id: str
    order_index: int
    top: PointPayload
    bottom: PointPayload


class PortalPointPayload(TypedDict):
    u: float
    v: float


class PortalGeometryPayload(TypedDict):
    top_left: PortalPointPayload
    top_right: PortalPointPayload
    bottom_left: PortalPointPayload
    bottom_right: PortalPointPayload


class PortalObservationPayload(TypedDict):
    portal_id: str
    kind: str
    geometry: PortalGeometryPayload
    evidence_status: str
    host_edge_ref: str | None


class AnnotationStatePayload(TypedDict):
    difficulty: NotRequired[list[str]]
    geometry_attempt_reason_text: str
    geometry_attempt_status: str | None
    model_issue: NotRequired[list[str]]
    pairs: list[PointPairPayload]
    portals: list[PortalObservationPayload]
    seam_anchor_pair_id: str | None
    scope_reason_codes: list[str]
    scope_reason_text: str
    worker_scope_observation: str | None


GEOMETRY_ATTEMPT_STATUSES = (
    "best_effort_complete",
    "partial",
    "not_drawable",
)
PORTAL_EVIDENCE_STATUSES = ("direct_visible", "inferred", "ambiguous")
PORTAL_KINDS = ("door", "architectural_opening", "window", "open_connection", "unknown")


class AnnotationStateError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        field: str,
        pair_id: str | None = None,
        pair_index: int | None = None,
        point_id: str | None = None,
        portal_id: str | None = None,
    ) -> None:
        self.code = code
        self.field = field
        self.pair_id = pair_id
        self.pair_index = pair_index
        self.point_id = point_id
        self.portal_id = portal_id
        super().__init__(code)


def empty_annotation_state(
    *, meta_schema_version: str = POC_META_SCHEMA_VERSION, task_mode: str = "manual"
) -> AnnotationStatePayload:
    state: AnnotationStatePayload = {
        "geometry_attempt_reason_text": "",
        "geometry_attempt_status": None,
        "pairs": [],
        "portals": [],
        "seam_anchor_pair_id": None,
        "scope_reason_codes": [],
        "scope_reason_text": "",
        "worker_scope_observation": None,
    }
    if meta_schema_version == META_SCHEMA_V1:
        state["difficulty"] = []
        if task_mode == "semi":
            state["model_issue"] = []
    elif meta_schema_version != POC_META_SCHEMA_VERSION:
        raise AnnotationStateError("annotation_schema_unsupported", field="schema_version")
    return state


def canonicalize_annotation_state(
    payload: object,
    *,
    meta_schema_version: str = POC_META_SCHEMA_VERSION,
    task_mode: str = "manual",
) -> AnnotationStatePayload:
    payload = _mapping(payload, field="state")
    expected_fields = {
        "geometry_attempt_reason_text",
        "geometry_attempt_status",
        "pairs",
        "portals",
        "seam_anchor_pair_id",
        "scope_reason_codes",
        "scope_reason_text",
        "worker_scope_observation",
    }
    if meta_schema_version == META_SCHEMA_V1:
        expected_fields.add("difficulty")
        if task_mode == "semi":
            expected_fields.add("model_issue")
        elif task_mode == "manual" and "model_issue" in payload:
            raise AnnotationStateError("annotation_model_issue_forbidden", field="model_issue")
        elif task_mode != "manual":
            raise AnnotationStateError("annotation_schema_unsupported", field="task_mode")
    elif meta_schema_version != POC_META_SCHEMA_VERSION:
        raise AnnotationStateError("annotation_schema_unsupported", field="schema_version")
    _require_fields(
        payload,
        expected_fields,
        "state",
    )
    raw_pairs = payload["pairs"]
    if not isinstance(raw_pairs, list):
        raise AnnotationStateError("annotation_field_type_invalid", field="pairs")

    indexed_pairs = [
        (pair_index, _point_pair(raw_pair, pair_index=pair_index))
        for pair_index, raw_pair in enumerate(raw_pairs)
    ]
    pair_ids: set[str] = set()
    point_ids: set[str] = set()
    for pair_index, pair in indexed_pairs:
        pair_id = pair["pair_id"]
        if pair_id in pair_ids:
            raise AnnotationStateError(
                "annotation_pair_id_duplicate",
                field="pair_id",
                pair_id=pair_id,
                pair_index=pair_index,
            )
        pair_ids.add(pair_id)
        for label in ("top", "bottom"):
            point_id = pair[label]["point_id"]
            if point_id in point_ids:
                raise AnnotationStateError(
                    "annotation_point_id_duplicate",
                    field=f"{label}.point_id",
                    pair_id=pair_id,
                    pair_index=pair_index,
                )
            point_ids.add(point_id)

    indexed_pairs.sort(key=lambda item: item[1]["order_index"])
    for expected_order, (pair_index, pair) in enumerate(indexed_pairs):
        if pair["order_index"] != expected_order:
            raise AnnotationStateError(
                "annotation_order_index_invalid",
                field="order_index",
                pair_id=pair["pair_id"],
                pair_index=pair_index,
            )

    seam_anchor_pair_id = _seam_anchor(payload["seam_anchor_pair_id"], pair_ids)
    portals = _portals(payload["portals"], pair_ids)
    worker_scope_observation, scope_reason_codes, scope_reason_text = _scope(
        observation=payload["worker_scope_observation"],
        reasons=payload["scope_reason_codes"],
        reason_text=payload["scope_reason_text"],
    )
    pairs = [pair for _pair_index, pair in indexed_pairs]
    attempt = _optional_enum(
        payload["geometry_attempt_status"],
        GEOMETRY_ATTEMPT_STATUSES,
        code="annotation_attempt_invalid",
        field="geometry_attempt_status",
    )
    reason = payload["geometry_attempt_reason_text"]
    if not isinstance(reason, str):
        raise AnnotationStateError(
            "annotation_attempt_invalid", field="geometry_attempt_reason_text"
        )
    reason = reason.strip()
    if attempt != "not_drawable" and reason:
        raise AnnotationStateError(
            "annotation_attempt_invalid", field="geometry_attempt_reason_text"
        )
    canonical: AnnotationStatePayload = {
        "geometry_attempt_reason_text": reason,
        "geometry_attempt_status": attempt,
        "pairs": pairs,
        "portals": portals,
        "seam_anchor_pair_id": seam_anchor_pair_id,
        "scope_reason_codes": scope_reason_codes,
        "scope_reason_text": scope_reason_text,
        "worker_scope_observation": worker_scope_observation,
    }
    if meta_schema_version == META_SCHEMA_V1:
        canonical["difficulty"] = _meta_codes(
            payload["difficulty"],
            allowed=DIFFICULTY_CODES,
            exclusive="trivial",
            code="annotation_difficulty_invalid",
            field="difficulty",
        )
        if task_mode == "semi":
            canonical["model_issue"] = _meta_codes(
                payload["model_issue"],
                allowed=MODEL_ISSUE_CODES,
                exclusive="acceptable",
                code="annotation_model_issue_invalid",
                field="model_issue",
            )
    return canonical


def validate_annotation_submission(
    payload: object,
    *,
    meta_schema_version: str = POC_META_SCHEMA_VERSION,
    task_mode: str = "manual",
) -> AnnotationStatePayload:
    canonical = canonicalize_annotation_state(
        payload, meta_schema_version=meta_schema_version, task_mode=task_mode
    )
    if (
        canonical["geometry_attempt_status"] is None
        or canonical["worker_scope_observation"] is None
    ):
        raise AnnotationStateError("annotation_submission_incomplete", field="state")
    if (
        canonical["worker_scope_observation"]
        in {
            "needs_scope_review",
            "representation_oos",
        }
        and not canonical["scope_reason_codes"]
    ):
        raise AnnotationStateError("annotation_submission_incomplete", field="scope_reason_codes")
    if "other" in canonical["scope_reason_codes"] and not canonical["scope_reason_text"]:
        raise AnnotationStateError("annotation_submission_incomplete", field="scope_reason_text")
    if canonical["geometry_attempt_status"] == "best_effort_complete" and not canonical["pairs"]:
        raise AnnotationStateError("annotation_submission_incomplete", field="pairs")
    if (
        canonical["geometry_attempt_status"] == "not_drawable"
        and not canonical["geometry_attempt_reason_text"]
    ):
        raise AnnotationStateError(
            "annotation_submission_incomplete", field="geometry_attempt_reason_text"
        )
    if meta_schema_version == META_SCHEMA_V1 and not canonical["difficulty"]:
        raise AnnotationStateError("annotation_submission_incomplete", field="difficulty")
    if (
        meta_schema_version == META_SCHEMA_V1
        and task_mode == "semi"
        and not canonical["model_issue"]
    ):
        raise AnnotationStateError("annotation_submission_incomplete", field="model_issue")
    return canonical


def canonical_annotation_json(
    payload: object,
    *,
    meta_schema_version: str = POC_META_SCHEMA_VERSION,
    task_mode: str = "manual",
) -> bytes:
    canonical = canonicalize_annotation_state(
        payload, meta_schema_version=meta_schema_version, task_mode=task_mode
    )
    attempt_reason = _json_string(canonical["geometry_attempt_reason_text"])
    attempt = _json_nullable_string(canonical["geometry_attempt_status"])
    pairs = ",".join(_canonical_pair_json(pair) for pair in canonical["pairs"])
    portals = ",".join(_canonical_portal_json(portal) for portal in canonical["portals"])
    seam_anchor = _json_nullable_string(canonical["seam_anchor_pair_id"])
    reason_codes = ",".join(_json_string(code) for code in canonical["scope_reason_codes"])
    reason_text = _json_string(canonical["scope_reason_text"])
    observation = _json_nullable_string(canonical["worker_scope_observation"])
    difficulty_field = ""
    if "difficulty" in canonical:
        difficulty = ",".join(_json_string(code) for code in canonical["difficulty"])
        difficulty_field = f'"difficulty":[{difficulty}],'
    model_issue_field = ""
    if "model_issue" in canonical:
        model_issue = ",".join(_json_string(code) for code in canonical["model_issue"])
        model_issue_field = f',"model_issue":[{model_issue}]'
    return (
        f'{{{difficulty_field}"geometry_attempt_reason_text":{attempt_reason},'
        f'"geometry_attempt_status":{attempt},"pairs":[{pairs}],"portals":[{portals}],'
        f'"seam_anchor_pair_id":{seam_anchor},"scope_reason_codes":[{reason_codes}],'
        f'"scope_reason_text":{reason_text},"worker_scope_observation":{observation}'
        f"{model_issue_field}}}"
    ).encode()


def annotation_state_sha(
    payload: object,
    *,
    meta_schema_version: str = POC_META_SCHEMA_VERSION,
    task_mode: str = "manual",
) -> str:
    return sha256(
        canonical_annotation_json(
            payload, meta_schema_version=meta_schema_version, task_mode=task_mode
        )
    ).hexdigest()


def _meta_codes(
    value: object,
    *,
    allowed: tuple[str, ...],
    exclusive: str,
    code: str,
    field: str,
) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AnnotationStateError(code, field=field)
    selected = set(value)
    if len(selected) != len(value) or not selected <= set(allowed):
        raise AnnotationStateError(code, field=field)
    if exclusive in selected and len(selected) > 1:
        raise AnnotationStateError(code, field=field)
    return [item for item in allowed if item in selected]


def normalized_to_pixels(*, u: float, v: float, width: int, height: int) -> tuple[float, float]:
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise AnnotationStateError("annotation_media_dimension_invalid", field="width")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise AnnotationStateError("annotation_media_dimension_invalid", field="height")
    return _coordinate(u, field="u") * width, _coordinate(v, field="v") * height


def _seam_anchor(value: object, pair_ids: set[str]) -> str | None:
    if not pair_ids:
        if value is None:
            return None
        raise AnnotationStateError("annotation_seam_anchor_invalid", field="seam_anchor_pair_id")
    if value is None:
        raise AnnotationStateError("annotation_seam_anchor_invalid", field="seam_anchor_pair_id")
    seam_anchor_pair_id = _stable_id(value, "seam_anchor_pair_id")
    if seam_anchor_pair_id not in pair_ids:
        raise AnnotationStateError("annotation_seam_anchor_invalid", field="seam_anchor_pair_id")
    return seam_anchor_pair_id


def _scope(
    *, observation: object, reasons: object, reason_text: object
) -> tuple[str | None, list[str], str]:
    worker_scope_observation = _optional_enum(
        observation,
        SCOPE_OBSERVATIONS,
        code="annotation_scope_invalid",
        field="worker_scope_observation",
    )
    if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
        raise AnnotationStateError("annotation_scope_invalid", field="scope_reason_codes")
    reason_set = set(reasons)
    if len(reason_set) != len(reasons) or not reason_set <= set(SCOPE_REASON_CODES):
        raise AnnotationStateError("annotation_scope_invalid", field="scope_reason_codes")
    if not isinstance(reason_text, str):
        raise AnnotationStateError("annotation_scope_invalid", field="scope_reason_text")
    text = reason_text.strip()
    ordered_reasons = [code for code in SCOPE_REASON_CODES if code in reason_set]
    if worker_scope_observation in {None, "annotatable"} and ordered_reasons:
        raise AnnotationStateError("annotation_scope_invalid", field="scope_reason_codes")
    if "other" not in reason_set and text:
        raise AnnotationStateError("annotation_scope_invalid", field="scope_reason_text")
    return worker_scope_observation, ordered_reasons, text


def _portals(value: object, pair_ids: set[str]) -> list[PortalObservationPayload]:
    if not isinstance(value, list):
        raise AnnotationStateError("annotation_field_type_invalid", field="portals")
    portals: list[PortalObservationPayload] = []
    for item in value:
        portal_locator = _id_locator(item.get("portal_id")) if isinstance(item, Mapping) else None
        try:
            portals.append(_portal(item))
        except AnnotationStateError as error:
            raise AnnotationStateError(
                error.code,
                field=error.field,
                pair_id=error.pair_id,
                pair_index=error.pair_index,
                point_id=error.point_id,
                portal_id=portal_locator,
            ) from error
    portal_ids: set[str] = set()
    for portal in portals:
        portal_id = portal["portal_id"]
        if portal_id in portal_ids:
            raise AnnotationStateError(
                "annotation_portal_id_duplicate",
                field="portals.portal_id",
                portal_id=portal_id,
            )
        portal_ids.add(portal_id)
        host_edge_ref = portal["host_edge_ref"]
        if host_edge_ref is not None and host_edge_ref not in pair_ids:
            raise AnnotationStateError(
                "annotation_portal_host_edge_invalid",
                field="portals.host_edge_ref",
                portal_id=portal_id,
            )
    return sorted(portals, key=lambda portal: portal["portal_id"])


def _portal(value: object) -> PortalObservationPayload:
    portal = _mapping(value, field="portals")
    _require_fields(
        portal,
        {"portal_id", "kind", "geometry", "evidence_status", "host_edge_ref"},
        "portal",
    )
    host_edge_ref = portal["host_edge_ref"]
    if host_edge_ref is not None:
        host_edge_ref = _stable_id(host_edge_ref, "portals.host_edge_ref")
    return {
        "portal_id": _stable_id(portal["portal_id"], "portals.portal_id"),
        "kind": _required_enum(
            portal["kind"], PORTAL_KINDS, code="annotation_portal_invalid", field="portals.kind"
        ),
        "geometry": _portal_geometry(portal["geometry"]),
        "evidence_status": _required_enum(
            portal["evidence_status"],
            PORTAL_EVIDENCE_STATUSES,
            code="annotation_portal_invalid",
            field="portals.evidence_status",
        ),
        "host_edge_ref": host_edge_ref,
    }


def _portal_geometry(value: object) -> PortalGeometryPayload:
    geometry = _mapping(value, field="portals.geometry")
    corners = {"top_left", "top_right", "bottom_left", "bottom_right"}
    _require_fields(geometry, corners, "portal_geometry")
    return {
        "top_left": _portal_point(geometry["top_left"], "top_left"),
        "top_right": _portal_point(geometry["top_right"], "top_right"),
        "bottom_left": _portal_point(geometry["bottom_left"], "bottom_left"),
        "bottom_right": _portal_point(geometry["bottom_right"], "bottom_right"),
    }


def _portal_point(value: object, corner: str) -> PortalPointPayload:
    point = _mapping(value, field=f"portals.geometry.{corner}")
    _require_fields(point, {"u", "v"}, "portal_point")
    return {
        "u": _coordinate(point["u"], field=f"portals.geometry.{corner}.u"),
        "v": _coordinate(point["v"], field=f"portals.geometry.{corner}.v"),
    }


def _optional_enum(value: object, allowed: tuple[str, ...], *, code: str, field: str) -> str | None:
    if value is None:
        return None
    return _required_enum(value, allowed, code=code, field=field)


def _required_enum(value: object, allowed: tuple[str, ...], *, code: str, field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise AnnotationStateError(code, field=field)
    return value


def _point_pair(value: object, *, pair_index: int) -> PointPairPayload:
    pair = _mapping(value, field="pairs", pair_index=pair_index)
    pair_locator = _id_locator(pair.get("pair_id"))
    _require_fields(
        pair,
        {"pair_id", "order_index", "top", "bottom"},
        "pair",
        pair_id=pair_locator,
        pair_index=pair_index,
    )
    pair_id = _stable_id(pair["pair_id"], "pair_id", pair_id=pair_locator, pair_index=pair_index)
    order_index = pair["order_index"]
    if isinstance(order_index, bool) or not isinstance(order_index, int):
        raise AnnotationStateError(
            "annotation_order_index_invalid",
            field="order_index",
            pair_id=pair_id,
            pair_index=pair_index,
        )
    return {
        "pair_id": pair_id,
        "order_index": order_index,
        "top": _point(pair["top"], "top", pair_id=pair_id, pair_index=pair_index),
        "bottom": _point(pair["bottom"], "bottom", pair_id=pair_id, pair_index=pair_index),
    }


def _point(value: object, label: str, *, pair_id: str, pair_index: int) -> PointPayload:
    point = _mapping(value, field=label, pair_id=pair_id, pair_index=pair_index)
    _require_fields(
        point,
        {"point_id", "u", "v"},
        label,
        pair_id=pair_id,
        pair_index=pair_index,
    )
    point_id = _stable_id(
        point["point_id"],
        f"{label}.point_id",
        pair_id=pair_id,
        pair_index=pair_index,
    )
    return {
        "point_id": point_id,
        "u": _coordinate(
            point["u"],
            field=f"{label}.u",
            pair_id=pair_id,
            pair_index=pair_index,
            point_id=point_id,
        ),
        "v": _coordinate(
            point["v"],
            field=f"{label}.v",
            pair_id=pair_id,
            pair_index=pair_index,
            point_id=point_id,
        ),
    }


def _coordinate(
    value: object,
    *,
    field: str,
    pair_id: str | None = None,
    pair_index: int | None = None,
    point_id: str | None = None,
) -> float:
    axis = field.rsplit(".", 1)[-1]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnnotationStateError(
            "annotation_coordinate_invalid",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
            point_id=point_id,
        )
    try:
        coordinate = float(value)
    except OverflowError as error:
        raise AnnotationStateError(
            "annotation_coordinate_non_finite",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
            point_id=point_id,
        ) from error
    if not isfinite(coordinate):
        raise AnnotationStateError(
            "annotation_coordinate_non_finite",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
            point_id=point_id,
        )
    if axis == "u" and not 0 <= coordinate < 1:
        raise AnnotationStateError(
            "annotation_coordinate_out_of_range",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
            point_id=point_id,
        )
    if axis == "v" and not 0 <= coordinate <= 1:
        raise AnnotationStateError(
            "annotation_coordinate_out_of_range",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
            point_id=point_id,
        )
    return 0.0 if coordinate == 0 else coordinate


def _stable_id(
    value: object,
    field: str,
    *,
    pair_id: str | None = None,
    pair_index: int | None = None,
) -> str:
    if not isinstance(value, str):
        raise AnnotationStateError(
            "annotation_id_invalid", field=field, pair_id=pair_id, pair_index=pair_index
        )
    try:
        return str(UUID(value))
    except ValueError as error:
        raise AnnotationStateError(
            "annotation_id_invalid", field=field, pair_id=pair_id, pair_index=pair_index
        ) from error


def _mapping(
    value: object,
    *,
    field: str,
    pair_id: str | None = None,
    pair_index: int | None = None,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise AnnotationStateError(
            "annotation_field_type_invalid",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
        )
    return value


def _require_fields(
    value: Mapping[str, object],
    expected: set[str],
    label: str,
    *,
    pair_id: str | None = None,
    pair_index: int | None = None,
) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    if unknown:
        field = f"{label}.{unknown[0]}" if label in {"top", "bottom"} else unknown[0]
        raise AnnotationStateError(
            "annotation_field_unknown",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
        )
    missing = sorted(expected - actual)
    if missing:
        field = f"{label}.{missing[0]}" if label in {"top", "bottom"} else missing[0]
        raise AnnotationStateError(
            "annotation_field_missing",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
        )


def _id_locator(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _canonical_pair_json(pair: PointPairPayload) -> str:
    bottom = _canonical_point_json(pair["bottom"])
    top = _canonical_point_json(pair["top"])
    pair_id = _json_string(pair["pair_id"])
    return (
        f'{{"bottom":{bottom},"order_index":{pair["order_index"]},"pair_id":{pair_id},"top":{top}}}'
    )


def _canonical_point_json(point: PointPayload) -> str:
    point_id = _json_string(point["point_id"])
    return (
        f'{{"point_id":{point_id},"u":{_ecmascript_number(point["u"])},'
        f'"v":{_ecmascript_number(point["v"])}}}'
    )


def _canonical_portal_json(portal: PortalObservationPayload) -> str:
    geometry = portal["geometry"]
    canonical_geometry = (
        f'{{"bottom_left":{_canonical_portal_point_json(geometry["bottom_left"])},'
        f'"bottom_right":{_canonical_portal_point_json(geometry["bottom_right"])},'
        f'"top_left":{_canonical_portal_point_json(geometry["top_left"])},'
        f'"top_right":{_canonical_portal_point_json(geometry["top_right"])}}}'
    )
    return (
        f'{{"evidence_status":{_json_string(portal["evidence_status"])},'
        f'"geometry":{canonical_geometry},'
        f'"host_edge_ref":{_json_nullable_string(portal["host_edge_ref"])},'
        f'"kind":{_json_string(portal["kind"])},'
        f'"portal_id":{_json_string(portal["portal_id"])}}}'
    )


def _canonical_portal_point_json(point: PortalPointPayload) -> str:
    return f'{{"u":{_ecmascript_number(point["u"])},"v":{_ecmascript_number(point["v"])}}}'


def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_nullable_string(value: str | None) -> str:
    return "null" if value is None else _json_string(value)


def _ecmascript_number(value: float) -> str:
    if value == 0:
        return "0"
    text = repr(value).lower()
    if 1e-6 <= abs(value) < 1e21:
        fixed = format(Decimal(text), "f")
        return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
    mantissa, exponent = text.split("e")
    mantissa = mantissa.rstrip("0").rstrip(".")
    exponent_value = int(exponent)
    exponent_text = f"+{exponent_value}" if exponent_value >= 0 else str(exponent_value)
    return f"{mantissa}e{exponent_text}"
