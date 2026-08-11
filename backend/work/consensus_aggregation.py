from __future__ import annotations

from collections.abc import Callable
from functools import cache
from itertools import combinations
from math import inf
from typing import TypedDict, cast

from django.core.exceptions import ValidationError

from .scope_aggregation import CONSENSUS_POLICY_V2, ConsensusPolicy


class ComponentAggregation(TypedDict):
    cluster_revision_ids: list[str]
    margin: int
    medoid_revision_id: str | None
    primary_support: int
    secondary_support: int
    state: str


class CanonicalComponentAggregation(TypedDict):
    geometry: ComponentAggregation
    portal: ComponentAggregation


def _axis_delta(left: dict[str, object], right: dict[str, object]) -> float:
    left_u, right_u = float(cast(float, left["u"])), float(cast(float, right["u"]))
    horizontal = abs(left_u - right_u)
    return max(
        min(horizontal, 1 - horizontal),
        abs(float(cast(float, left["v"])) - float(cast(float, right["v"]))),
    )


def _ordered_pairs(item: dict[str, object]) -> list[dict[str, object]]:
    state = cast(dict[str, object], item["state"])
    return sorted(
        cast(list[dict[str, object]], state.get("pairs", [])),
        key=lambda pair: cast(int, pair["order_index"]),
    )


def _pair_alignment(
    left: dict[str, object], right: dict[str, object]
) -> tuple[float, dict[str, str]]:
    left_pairs = _ordered_pairs(left)
    right_pairs = _ordered_pairs(right)
    if not left_pairs or len(left_pairs) != len(right_pairs):
        return inf, {}
    best_distance = inf
    best_mapping: dict[str, str] = {}
    for reverse in (False, True):
        oriented = list(reversed(right_pairs)) if reverse else right_pairs
        for shift in range(len(oriented)):
            aligned = oriented[shift:] + oriented[:shift]
            distance = max(
                _axis_delta(
                    cast(dict[str, object], left_pair[side]),
                    cast(dict[str, object], right_pair[side]),
                )
                for left_pair, right_pair in zip(left_pairs, aligned, strict=True)
                for side in ("top", "bottom")
            )
            mapping = {
                cast(str, left_pair["pair_id"]): cast(str, right_pair["pair_id"])
                for left_pair, right_pair in zip(left_pairs, aligned, strict=True)
            }
            if (distance, sorted(mapping.items())) < (best_distance, sorted(best_mapping.items())):
                best_distance, best_mapping = distance, mapping
    return best_distance, best_mapping


def _geometry_distance(left: dict[str, object], right: dict[str, object]) -> float:
    return _pair_alignment(left, right)[0]


def _portals(item: dict[str, object]) -> list[dict[str, object]]:
    state = cast(dict[str, object], item["state"])
    return cast(list[dict[str, object]], state.get("portals", []))


def _portal_pair_distance(
    left: dict[str, object],
    right: dict[str, object],
    pair_mapping: dict[str, str],
) -> float:
    if left.get("kind") != right.get("kind"):
        return inf
    left_host, right_host = left.get("host_edge_ref"), right.get("host_edge_ref")
    if (
        left_host is not None
        and right_host is not None
        and pair_mapping.get(str(left_host)) != str(right_host)
    ):
        return inf
    left_geometry = cast(dict[str, dict[str, object]], left["geometry"])
    right_geometry = cast(dict[str, dict[str, object]], right["geometry"])
    return max(
        _axis_delta(left_geometry[corner], right_geometry[corner])
        for corner in ("top_left", "top_right", "bottom_left", "bottom_right")
    )


def _portal_distance(left: dict[str, object], right: dict[str, object]) -> float:
    left_portals, right_portals = _portals(left), _portals(right)
    if len(left_portals) != len(right_portals):
        return inf
    if not left_portals:
        return 0
    _geometry_delta, pair_mapping = _pair_alignment(left, right)
    costs = [
        [
            _portal_pair_distance(left_portal, right_portal, pair_mapping)
            for right_portal in right_portals
        ]
        for left_portal in left_portals
    ]

    @cache
    def minimum_bottleneck(left_index: int, used_right: int) -> float:
        if left_index == len(left_portals):
            return 0
        return min(
            max(
                costs[left_index][right_index], minimum_bottleneck(left_index + 1, used_right | bit)
            )
            for right_index in range(len(right_portals))
            if not used_right & (bit := 1 << right_index)
        )

    return minimum_bottleneck(0, 0)


def _largest_cliques(
    indexes: tuple[int, ...], distances: list[list[float]], threshold: float
) -> list[tuple[int, ...]]:
    # ponytail: exact subsets are enough for small WorkBatch k; replace only if k grows materially.
    for size in range(len(indexes), 0, -1):
        cliques = [
            candidate
            for candidate in combinations(indexes, size)
            if all(
                distances[left][right] <= threshold + 1e-12
                for left, right in combinations(candidate, 2)
            )
        ]
        if cliques:
            return cliques
    return []


def _medoid(
    cluster: tuple[int, ...],
    items: list[dict[str, object]],
    distances: list[list[float]],
) -> str:
    index = min(
        cluster,
        key=lambda candidate: (
            sum(distances[candidate][other] for other in cluster),
            cast(str, items[candidate]["submitted_at"]),
            cast(str, items[candidate]["revision_id"]),
        ),
    )
    return cast(str, items[index]["revision_id"])


def _aggregate_component(
    *,
    all_inputs: list[dict[str, object]],
    component_inputs: list[dict[str, object]],
    distance: Callable[[dict[str, object], dict[str, object]], float],
    threshold: float,
    policy: ConsensusPolicy,
) -> ComponentAggregation:
    if not component_inputs:
        return {
            "cluster_revision_ids": [],
            "margin": 0,
            "medoid_revision_id": None,
            "primary_support": 0,
            "secondary_support": 0,
            "state": "not_evaluable",
        }
    distances = [[0.0 for _item in component_inputs] for _item in component_inputs]
    for left, right in combinations(range(len(component_inputs)), 2):
        distances[left][right] = distances[right][left] = distance(
            component_inputs[left], component_inputs[right]
        )
    primary_candidates = _largest_cliques(tuple(range(len(component_inputs))), distances, threshold)
    primary = min(
        primary_candidates,
        key=lambda cluster: [
            cast(str, component_inputs[index]["revision_id"]) for index in cluster
        ],
    )
    primary_support = len(primary)
    if len(primary_candidates) > 1:
        secondary_support = primary_support
    else:
        remaining = tuple(index for index in range(len(component_inputs)) if index not in primary)
        secondary = _largest_cliques(remaining, distances, threshold)
        secondary_support = len(secondary[0]) if secondary else 0
    margin = primary_support - secondary_support
    resolved = (
        len(primary_candidates) == 1
        and primary_support >= policy["minimum_primary_support"]
        and margin >= policy["minimum_cluster_margin"]
    )
    if resolved:
        state = "resolved"
    elif len(all_inputs) >= policy["k_max"]:
        state = "unresolved"
    else:
        state = "needs_more"
    return {
        "cluster_revision_ids": [
            cast(str, component_inputs[index]["revision_id"]) for index in primary
        ],
        "margin": margin,
        "medoid_revision_id": _medoid(primary, component_inputs, distances) if resolved else None,
        "primary_support": primary_support,
        "secondary_support": secondary_support,
        "state": state,
    }


def aggregate_canonical_components(
    *, inputs: list[dict[str, object]], policy: ConsensusPolicy
) -> CanonicalComponentAggregation:
    if policy.get("version") != CONSENSUS_POLICY_V2:
        raise ValidationError(
            "Consensus policy does not define component similarity.",
            code="consensus_similarity_unavailable",
        )
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("revision_id"), str)
        or not isinstance(item.get("submitted_at"), str)
        or not isinstance(item.get("state"), dict)
        for item in inputs
    ):
        raise ValidationError("Invalid consensus input.", code="consensus_input_invalid")
    geometry_inputs = [item for item in inputs if _ordered_pairs(item)]
    # An empty portal list is still a valid observation that no portal was found.
    portal_inputs = inputs
    return {
        "geometry": _aggregate_component(
            all_inputs=inputs,
            component_inputs=geometry_inputs,
            distance=_geometry_distance,
            threshold=policy["geometry_max_axis_delta"],
            policy=policy,
        ),
        "portal": _aggregate_component(
            all_inputs=inputs,
            component_inputs=portal_inputs,
            distance=_portal_distance,
            threshold=policy["portal_max_axis_delta"],
            policy=policy,
        ),
    }
