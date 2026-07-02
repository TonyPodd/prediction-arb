from __future__ import annotations

from collections import Counter
from pathlib import Path

from prediction_arb.review_store import DEFAULT_LABEL_FILE, DEFAULT_REVIEW_FILE, load_review_queue


def summarize_review_quality(
    candidate_path: Path = DEFAULT_REVIEW_FILE,
    label_path: Path = DEFAULT_LABEL_FILE,
    *,
    limit: int = 0,
    examples: int = 10,
) -> dict[str, object]:
    rows = load_review_queue(candidate_path, label_path, limit=limit)
    label_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    different_reason_counts: Counter[str] = Counter()
    different_warning_counts: Counter[str] = Counter()
    examples_by_label: dict[str, list[dict[str, object]]] = {"different_event": [], "unsure": [], "same_event": []}

    for row in rows:
        label = _label(row)
        if label:
            label_counts[label] += 1
        risk = row.get("risk", {}) if isinstance(row.get("risk"), dict) else {}
        candidate = row.get("candidate", {}) if isinstance(row.get("candidate"), dict) else {}
        risk_level = str(risk.get("risk_level") or "unknown")
        risk_counts[risk_level] += 1
        route = f"{candidate.get('buy_source', '')}->{candidate.get('sell_source', '')}"
        route_counts[route] += 1
        reasons = [str(item) for item in risk.get("reasons", []) if item] if isinstance(risk.get("reasons"), list) else []
        warnings = [str(item) for item in risk.get("match_warnings", []) if item] if isinstance(risk.get("match_warnings"), list) else []
        reason_counts.update(reasons)
        warning_counts.update(warnings)
        if label == "different_event":
            different_reason_counts.update(reasons)
            different_warning_counts.update(warnings)
        if label in examples_by_label and len(examples_by_label[label]) < examples:
            examples_by_label[label].append(_example(row))

    labeled_count = sum(label_counts.values())
    pending_count = len(rows) - labeled_count
    confirmed = label_counts["same_event"]
    false_positive = label_counts["different_event"]
    decisive = confirmed + false_positive

    return {
        "input": str(candidate_path),
        "labels": str(label_path),
        "total_candidates": len(rows),
        "labeled_count": labeled_count,
        "pending_count": pending_count,
        "label_counts": dict(label_counts),
        "risk_level_counts": dict(risk_counts),
        "route_counts": dict(route_counts),
        "reason_counts": dict(reason_counts),
        "warning_counts": dict(warning_counts),
        "different_event_reason_counts": dict(different_reason_counts),
        "different_event_warning_counts": dict(different_warning_counts),
        "same_event_rate": confirmed / decisive if decisive else None,
        "false_positive_rate": false_positive / decisive if decisive else None,
        "examples": examples_by_label,
    }


def _label(row: dict[str, object]) -> str | None:
    label = row.get("label")
    if isinstance(label, dict):
        value = str(label.get("label") or "")
        return value or None
    return None


def _example(row: dict[str, object]) -> dict[str, object]:
    candidate = row.get("candidate", {}) if isinstance(row.get("candidate"), dict) else {}
    risk = row.get("risk", {}) if isinstance(row.get("risk"), dict) else {}
    return {
        "review_id": row.get("review_id"),
        "route": f"{candidate.get('buy_source', '')}->{candidate.get('sell_source', '')}",
        "outcome": candidate.get("outcome"),
        "net_edge": candidate.get("net_edge"),
        "estimated_profit": (_float(candidate.get("net_edge")) or 0.0) * (_float(candidate.get("executable_size")) or 0.0),
        "risk_level": risk.get("risk_level"),
        "risk_score": risk.get("risk_score"),
        "reasons": risk.get("reasons", []),
        "buy_title": candidate.get("buy_title"),
        "sell_title": candidate.get("sell_title"),
    }


def _float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
