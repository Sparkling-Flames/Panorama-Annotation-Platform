"""Server canonicalization uses finite IEEE-754 binary64 coordinates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from math import isfinite
from typing import TypedDict
from uuid import UUID


class PointPayload(TypedDict):
    point_id: str
    u: float
    v: float


class PointPairPayload(TypedDict):
    pair_id: str
    order_index: int
    top: PointPayload
    bottom: PointPayload


class AnnotationStatePayload(TypedDict):
    pairs: list[PointPairPayload]
    seam_anchor_pair_id: str


class AnnotationStateError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        field: str,
        pair_id: str | None = None,
        pair_index: int | None = None,
    ) -> None:
        self.code = code
        self.field = field
        self.pair_id = pair_id
        self.pair_index = pair_index
        super().__init__(code)


def canonicalize_annotation_state(payload: object) -> AnnotationStatePayload:
    payload = _mapping(payload, field="state")
    _require_fields(payload, {"pairs", "seam_anchor_pair_id"}, "state")
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

    seam_anchor_pair_id = _stable_id(payload["seam_anchor_pair_id"], "seam_anchor_pair_id")
    if seam_anchor_pair_id not in pair_ids:
        raise AnnotationStateError("annotation_seam_anchor_invalid", field="seam_anchor_pair_id")
    pairs = [pair for _pair_index, pair in indexed_pairs]
    return {"pairs": pairs, "seam_anchor_pair_id": seam_anchor_pair_id}


def canonical_annotation_json(payload: object) -> bytes:
    canonical = canonicalize_annotation_state(payload)
    pairs = ",".join(_canonical_pair_json(pair) for pair in canonical["pairs"])
    seam_anchor = _json_string(canonical["seam_anchor_pair_id"])
    return f'{{"pairs":[{pairs}],"seam_anchor_pair_id":{seam_anchor}}}'.encode()


def annotation_state_sha(payload: object) -> str:
    return sha256(canonical_annotation_json(payload)).hexdigest()


def normalized_to_pixels(*, u: float, v: float, width: int, height: int) -> tuple[float, float]:
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise AnnotationStateError("annotation_media_dimension_invalid", field="width")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise AnnotationStateError("annotation_media_dimension_invalid", field="height")
    return _coordinate(u, field="u") * width, _coordinate(v, field="v") * height


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
    return {
        "point_id": _stable_id(
            point["point_id"],
            f"{label}.point_id",
            pair_id=pair_id,
            pair_index=pair_index,
        ),
        "u": _coordinate(point["u"], field=f"{label}.u", pair_id=pair_id, pair_index=pair_index),
        "v": _coordinate(point["v"], field=f"{label}.v", pair_id=pair_id, pair_index=pair_index),
    }


def _coordinate(
    value: object,
    *,
    field: str,
    pair_id: str | None = None,
    pair_index: int | None = None,
) -> float:
    axis = field.rsplit(".", 1)[-1]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnnotationStateError(
            "annotation_coordinate_invalid",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
        )
    try:
        coordinate = float(value)
    except OverflowError as error:
        raise AnnotationStateError(
            "annotation_coordinate_non_finite",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
        ) from error
    if not isfinite(coordinate):
        raise AnnotationStateError(
            "annotation_coordinate_non_finite",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
        )
    if axis == "u" and not 0 <= coordinate < 1:
        raise AnnotationStateError(
            "annotation_coordinate_out_of_range",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
        )
    if axis == "v" and not 0 <= coordinate <= 1:
        raise AnnotationStateError(
            "annotation_coordinate_out_of_range",
            field=field,
            pair_id=pair_id,
            pair_index=pair_index,
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


def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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
