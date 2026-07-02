from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

from prediction_arb.coverage import summarize_source_coverage
from prediction_arb.depth import scan_depth_candidates
from prediction_arb.matching import market_match_details
from prediction_arb.models import DepthCandidate, Market
from prediction_arb.scanner import _has_structural_mismatch


def build_health_report(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    *,
    size: float = 100.0,
    min_match_score: float = 0.25,
    min_net_edge: float = 0.005,
    safety_buffer: float = 0.002,
    fee_bps: float = 50.0,
    route_fixed_costs: dict[str, float] | None = None,
    route_cost_bps: dict[str, float] | None = None,
    min_profit: float = 1.0,
) -> dict[str, object]:
    pair_stats = _pair_stats(limitless_markets, polymarket_markets, min_match_score=min_match_score)
    no_cost = _scan_summary(
        limitless_markets,
        polymarket_markets,
        size=size,
        min_match_score=min_match_score,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        fee_bps=0.0,
        route_fixed_costs={},
        route_cost_bps={},
        min_profit=min_profit,
    )
    fees_only = _scan_summary(
        limitless_markets,
        polymarket_markets,
        size=size,
        min_match_score=min_match_score,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        fee_bps=fee_bps,
        route_fixed_costs={},
        route_cost_bps={},
        min_profit=min_profit,
    )
    full_costs = _scan_summary(
        limitless_markets,
        polymarket_markets,
        size=size,
        min_match_score=min_match_score,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        fee_bps=fee_bps,
        route_fixed_costs=route_fixed_costs or {},
        route_cost_bps=route_cost_bps or {},
        min_profit=min_profit,
    )
    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "parameters": {
            "size": size,
            "min_match_score": min_match_score,
            "min_net_edge": min_net_edge,
            "safety_buffer": safety_buffer,
            "fee_bps": fee_bps,
            "route_fixed_costs": route_fixed_costs or {},
            "route_cost_bps": route_cost_bps or {},
            "min_profit": min_profit,
        },
        "coverage": summarize_source_coverage(limitless_markets, polymarket_markets, example_limit=0),
        "matching": pair_stats,
        "scans": {
            "no_costs": no_cost,
            "fees_only": fees_only,
            "full_costs": full_costs,
        },
        "verdict": _verdict(limitless_markets, polymarket_markets, pair_stats, full_costs),
    }


def _pair_stats(limitless_markets: list[Market], polymarket_markets: list[Market], *, min_match_score: float) -> dict[str, object]:
    pair_count = 0
    text_candidates = 0
    structurally_compatible = 0
    warnings: Counter[str] = Counter()
    examples = []
    for left in limitless_markets:
        for right in polymarket_markets:
            pair_count += 1
            details = market_match_details(left, right)
            if details.score < min_match_score:
                continue
            text_candidates += 1
            warnings.update(details.warnings)
            if _has_structural_mismatch(details):
                continue
            structurally_compatible += 1
            if len(examples) < 8:
                examples.append(
                    {
                        "score": details.score,
                        "warnings": details.warnings,
                        "limitless": {"market_id": left.market_id, "title": left.title, "url": left.url},
                        "polymarket": {"market_id": right.market_id, "title": right.title, "url": right.url},
                    }
                )
    return {
        "pairs_checked": pair_count,
        "text_candidates": text_candidates,
        "structurally_compatible_pairs": structurally_compatible,
        "warning_counts": dict(warnings.most_common(20)),
        "examples": examples,
    }


def _scan_summary(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    *,
    size: float,
    min_match_score: float,
    min_net_edge: float,
    safety_buffer: float,
    fee_bps: float,
    route_fixed_costs: dict[str, float],
    route_cost_bps: dict[str, float],
    min_profit: float,
) -> dict[str, object]:
    rows = scan_depth_candidates(
        limitless_markets,
        polymarket_markets,
        size=size,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        min_match_score=min_match_score,
        allow_partial=False,
        fee_bps=fee_bps,
        min_profit=min_profit,
        route_fixed_costs=route_fixed_costs,
        route_cost_bps=route_cost_bps,
        include_filtered=True,
    )
    passing = [row for row in rows if not row.rejection_reason]
    rejected = [row for row in rows if row.rejection_reason]
    rejection_counts = Counter(str(row.rejection_reason) for row in rejected)
    return {
        "candidate_legs": len(rows),
        "passing_count": len(passing),
        "rejected_count": len(rejected),
        "rejection_counts": dict(rejection_counts.most_common(20)),
        "best_passing": _candidate_summary(passing[0]) if passing else None,
        "best_rejected": _candidate_summary(rejected[0]) if rejected else None,
        "best_any": _candidate_summary(rows[0]) if rows else None,
    }


def _candidate_summary(row: DepthCandidate) -> dict[str, object]:
    payload = _serializable(asdict(row))
    payload["estimated_profit"] = (row.net_edge or 0.0) * row.executable_size if row.net_edge is not None else None
    return payload


def _verdict(limitless_markets: list[Market], polymarket_markets: list[Market], pair_stats: dict[str, object], full_costs: dict[str, object]) -> dict[str, object]:
    if not limitless_markets or not polymarket_markets:
        return {"status": "source_empty", "message": "Один из источников не вернул рынки."}
    if int(pair_stats.get("structurally_compatible_pairs") or 0) <= 0:
        return {"status": "no_compatible_pairs", "message": "Рынки загружены, но matcher не нашел структурно совместимых пар."}
    if int(full_costs.get("candidate_legs") or 0) <= 0:
        return {"status": "no_orderbook_candidates", "message": "Совместимые пары есть, но стаканы/токены не дали depth-кандидатов."}
    if int(full_costs.get("passing_count") or 0) <= 0:
        return {"status": "all_filtered", "message": "Кандидаты есть, но после стаканов, комиссий и operational costs все отфильтрованы."}
    return {"status": "healthy_with_opportunities", "message": "Есть проходящие depth opportunities."}


def _serializable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serializable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
