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
    "outcome_subject_differs",
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
    fee_notes = [str(item) for item in (getattr(candidate, "fee_notes", None) or [])]

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
    if any("unknown" in note or "no_fee_field" in note for note in fee_notes):
        score += 20
        reasons.append("fee_model_uncertain")
    if any(note == "limitless_fee_curve_unknown_use_manual_fee_bps" for note in fee_notes):
        score += 10
        reasons.append("limitless_fee_curve_unknown")
    manual_bps = _manual_fee_bps(fee_notes)
    if manual_bps is not None and manual_bps < 25:
        score += 10
        reasons.append("low_manual_fee_buffer")
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
        "components": risk_components(candidate),
    }


def risk_components(candidate: object) -> dict[str, object]:
    fee_notes = [str(item) for item in (getattr(candidate, "fee_notes", None) or [])]
    net_edge = _float(getattr(candidate, "net_edge", None), 0.0)
    depth_edge = _float(getattr(candidate, "depth_edge", None), 0.0)
    fee_estimate = getattr(candidate, "fee_estimate", None)
    match_score = _float(getattr(candidate, "match_score", None), 0.0)
    warnings = [str(item) for item in (getattr(candidate, "match_warnings", None) or [])]
    buy_quote = getattr(candidate, "buy_quote", None)
    sell_quote = getattr(candidate, "sell_quote", None)

    fee_confidence = "high"
    if fee_estimate is None or any("unknown" in note or "no_fee_field" in note for note in fee_notes):
        fee_confidence = "low"
    elif any("manual_fee_bps" in note or "limitless_fee_curve_unknown" in note for note in fee_notes):
        fee_confidence = "medium"

    depth_level = "low"
    if not getattr(buy_quote, "complete", True) or not getattr(sell_quote, "complete", True):
        depth_level = "high"
    elif depth_edge - net_edge > 0.03:
        depth_level = "medium"

    match_level = "low"
    if warnings:
        match_level = "medium"
    if match_score < 0.35 or any(item in HARD_MATCH_WARNINGS for item in warnings):
        match_level = "high"

    return {
        "matching": {
            "level": match_level,
            "score": match_score,
            "warnings": warnings,
        },
        "fees": {
            "level": "low" if fee_confidence == "high" else ("medium" if fee_confidence == "medium" else "high"),
            "confidence": fee_confidence,
            "fee_estimate": fee_estimate,
            "notes": fee_notes,
        },
        "depth": {
            "level": depth_level,
            "depth_edge": getattr(candidate, "depth_edge", None),
            "net_edge": getattr(candidate, "net_edge", None),
            "buy_complete": getattr(buy_quote, "complete", None),
            "sell_complete": getattr(sell_quote, "complete", None),
        },
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


def _manual_fee_bps(notes: list[str]) -> float | None:
    for note in notes:
        if note.startswith("manual_fee_bps="):
            return _float(note.split("=", 1)[1], 0.0)
    return None
