from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from prediction_arb.depth import _pair_depth_opportunities
from prediction_arb.matching import MatchDetails, market_match_details
from prediction_arb.models import DepthCandidate, Market, OrderBook
from prediction_arb.near import append_near_opportunities, select_near_opportunities
from prediction_arb.scanner import _has_structural_mismatch


DEFAULT_RESEARCH_FILE = Path("data/research-monitor.jsonl")


def build_research_snapshot(
    *,
    scope: str,
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    size: float,
    min_net_edge: float,
    min_profit: float,
    safety_buffer: float,
    fee_bps: float,
    route_fixed_costs: dict[str, float] | None = None,
    route_cost_bps: dict[str, float] | None = None,
    min_match_score: float = 0.25,
    near_min_edge: float = 0.0,
    top: int = 30,
    max_depth_pairs: int = 40,
    near_output: Path | None = None,
    save_near: bool = True,
) -> dict[str, object]:
    pairs = selected_matching_pairs(
        limitless_markets,
        polymarket_markets,
        min_match_score=min_match_score,
        max_pairs=max_depth_pairs,
    )
    rows = _scan_depth_pairs(
        pairs,
        size=size,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        fee_bps=fee_bps,
        min_profit=min_profit,
        route_fixed_costs=route_fixed_costs or {},
        route_cost_bps=route_cost_bps or {},
    )
    near = select_near_opportunities(rows, min_edge=near_min_edge, top=top)
    saved = append_near_opportunities(near, near_output, source="research-monitor") if save_near and near_output else []
    passing = [row for row in rows if not row.rejection_reason]
    rejected = [row for row in rows if row.rejection_reason]
    rejection_counts = Counter(str(row.rejection_reason) for row in rejected)

    return {
        "type": "research_snapshot",
        "scope": scope,
        "detected_at": datetime.now(tz=timezone.utc).isoformat(),
        "size": size,
        "parameters": {
            "min_net_edge": min_net_edge,
            "min_profit": min_profit,
            "safety_buffer": safety_buffer,
            "fee_bps": fee_bps,
            "route_fixed_costs": route_fixed_costs or {},
            "route_cost_bps": route_cost_bps or {},
            "min_match_score": min_match_score,
            "near_min_edge": near_min_edge,
            "top": top,
            "max_depth_pairs": max_depth_pairs,
        },
        "source_counts": {
            "limitless": len(limitless_markets),
            "polymarket": len(polymarket_markets),
        },
        "matching": matching_summary(limitless_markets, polymarket_markets, min_match_score=min_match_score),
        "depth_pairs_scanned": len(pairs),
        "candidate_count": len(rows),
        "passing_count": len(passing),
        "rejected_count": len(rejected),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "near_count": len(near),
        "near_saved_count": len(saved),
        "best_any": _candidate_payload(rows[0]) if rows else None,
        "best_near": _candidate_payload(near[0]) if near else None,
        "near": [_candidate_payload(row) for row in near],
    }


def matching_summary(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    *,
    min_match_score: float = 0.25,
) -> dict[str, object]:
    text_candidates = 0
    compatible = 0
    warning_counts: Counter[str] = Counter()

    for left in limitless_markets:
        for right in polymarket_markets:
            details = market_match_details(left, right)
            if details.score < min_match_score:
                continue
            text_candidates += 1
            warning_counts.update(details.warnings)
            if not _has_structural_mismatch(details):
                compatible += 1

    return {
        "pairs_checked": len(limitless_markets) * len(polymarket_markets),
        "text_candidates": text_candidates,
        "structurally_compatible_pairs": compatible,
        "warning_counts": dict(sorted(warning_counts.items())),
    }


def selected_matching_pairs(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    *,
    min_match_score: float = 0.25,
    max_pairs: int = 40,
) -> list[tuple[Market, Market, MatchDetails]]:
    pairs: list[tuple[Market, Market, MatchDetails]] = []
    for left in limitless_markets:
        for right in polymarket_markets:
            details = market_match_details(left, right)
            if details.score < min_match_score or _has_structural_mismatch(details):
                continue
            pairs.append((left, right, details))
    pairs.sort(key=lambda item: (item[2].score, len(item[2].shared_tokens)), reverse=True)
    return pairs[:max_pairs] if max_pairs > 0 else pairs


def _scan_depth_pairs(
    pairs: list[tuple[Market, Market, MatchDetails]],
    *,
    size: float,
    min_net_edge: float,
    safety_buffer: float,
    fee_bps: float,
    min_profit: float,
    route_fixed_costs: dict[str, float],
    route_cost_bps: dict[str, float],
) -> list[DepthCandidate]:
    detected_at = datetime.now(tz=timezone.utc)
    rows: list[DepthCandidate] = []
    book_cache: dict[tuple[str, str, str], OrderBook | None] = {}
    for left, right, details in pairs:
        for outcome in ("YES", "NO"):
            rows.extend(
                _pair_depth_opportunities(
                    left,
                    right,
                    details,
                    outcome,
                    size,
                    min_net_edge,
                    safety_buffer,
                    False,
                    fee_bps,
                    min_profit,
                    route_fixed_costs,
                    route_cost_bps,
                    True,
                    detected_at,
                    book_cache,
                )
            )
    return sorted(rows, key=lambda item: item.net_edge if item.net_edge is not None else -999.0, reverse=True)


def _candidate_payload(row: DepthCandidate) -> dict[str, object]:
    payload = _serializable(asdict(row))
    payload["estimated_profit"] = (row.net_edge or 0.0) * row.executable_size if row.net_edge is not None else None
    payload["route"] = f"{row.buy_source}->{row.sell_source}"
    return payload


def _serializable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serializable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
