from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256


SOFT_MATCH_WARNINGS = {"price_source_differs", "price_pair_differs"}
HARD_MATCH_WARNINGS = {
    "condition_kind_differs",
    "asset_differs",
    "direction_differs",
    "threshold_differs",
    "interval_differs",
    "deadline_differs",
}


def candidate_review_id(candidate: object) -> str:
    parts = [
        str(getattr(candidate, "outcome", "")),
        str(getattr(candidate, "buy_source", "")),
        str(getattr(candidate, "buy_market_id", "")),
        str(getattr(candidate, "sell_source", "")),
        str(getattr(candidate, "sell_market_id", "")),
        str(getattr(candidate, "detected_at", "")),
    ]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def assess_candidate_risk(candidate: object) -> dict[str, object]:
    score = 0
    reasons: list[str] = []
    warnings = set(str(item) for item in (getattr(candidate, "match_warnings", None) or []))

    if warnings & HARD_MATCH_WARNINGS:
        score += 60
        reasons.append("hard_structural_warning")
    if "price_source_differs" in warnings:
        score += 20
        reasons.append("price_source_differs")
    if "price_pair_differs" in warnings:
        score += 15
        reasons.append("price_pair_differs")

    match_score = _float(getattr(candidate, "match_score", None), 1.0)
    if match_score < 0.35:
        score += 20
        reasons.append("low_match_score")
    elif match_score < 0.5:
        score += 10
        reasons.append("medium_match_score")

    net_edge = _float(getattr(candidate, "net_edge", None), 0.0)
    depth_edge = _float(getattr(candidate, "depth_edge", None), net_edge)
    top_edge = _float(getattr(candidate, "top_of_book_edge", None), depth_edge)
    if net_edge >= 0.50:
        score += 60
        reasons.append("extreme_net_edge")
    elif net_edge >= 0.25:
        score += 35
        reasons.append("very_high_net_edge")
    elif net_edge >= 0.10:
        score += 15
        reasons.append("high_net_edge")

    if abs(top_edge - depth_edge) >= 0.10:
        score += 15
        reasons.append("large_top_depth_gap")
    if getattr(candidate, "fee_estimate", None) is None:
        score += 10
        reasons.append("fee_estimate_missing")
    if getattr(candidate, "rejection_reason", None):
        score += 10
        reasons.append("filtered_candidate")

    level = "low"
    if score >= 60:
        level = "high"
    elif score >= 25:
        level = "medium"

    return {
        "review_id": candidate_review_id(candidate),
        "risk_score": score,
        "risk_level": level,
        "manual_review": score >= 25 or bool(warnings & SOFT_MATCH_WARNINGS),
        "reasons": reasons,
        "match_warnings": sorted(warnings),
    }


def candidate_to_dict(candidate: object) -> dict[str, object]:
    try:
        return asdict(candidate)
    except TypeError:
        if isinstance(candidate, dict):
            return dict(candidate)
        return dict(getattr(candidate, "__dict__", {}))


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
