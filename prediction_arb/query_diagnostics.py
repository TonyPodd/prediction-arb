from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

from prediction_arb.depth import scan_depth_candidates
from prediction_arb.matching import market_match_details
from prediction_arb.models import DepthCandidate, Market
from prediction_arb.scanner import _has_structural_mismatch


def build_query_diagnostic(
    *,
    query: str,
    kalshi_markets: list[Market],
    polymarket_markets: list[Market],
    size: float,
    min_match_score: float,
    min_net_edge: float,
    min_profit: float,
    safety_buffer: float,
    fee_bps: float,
    route_fixed_costs: dict[str, float],
    route_cost_bps: dict[str, float],
    max_depth_pairs: int,
) -> dict[str, object]:
    matching = _matching_funnel(kalshi_markets, polymarket_markets, min_match_score=min_match_score)
    no_cost_rows = scan_depth_candidates(
        kalshi_markets,
        polymarket_markets,
        size=size,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        min_match_score=min_match_score,
        fee_bps=0.0,
        min_profit=0.0,
        route_fixed_costs={},
        route_cost_bps={},
        include_filtered=True,
        max_depth_pairs=max_depth_pairs,
    )
    full_rows = scan_depth_candidates(
        kalshi_markets,
        polymarket_markets,
        size=size,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        min_match_score=min_match_score,
        fee_bps=fee_bps,
        min_profit=min_profit,
        route_fixed_costs=route_fixed_costs,
        route_cost_bps=route_cost_bps,
        include_filtered=True,
        max_depth_pairs=max_depth_pairs,
    )
    full_passing = [row for row in full_rows if not row.rejection_reason]
    full_rejected = [row for row in full_rows if row.rejection_reason]
    return {
        "type": "query_diagnostic",
        "query": query,
        "detected_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_counts": {"kalshi": len(kalshi_markets), "polymarket": len(polymarket_markets)},
        "matching": matching,
        "no_costs": _scan_summary(no_cost_rows),
        "full_costs": _scan_summary(full_rows),
        "passing_count": len(full_passing),
        "rejection_counts": dict(Counter(str(row.rejection_reason) for row in full_rejected).most_common(20)),
        "best_gross": _candidate_payload(no_cost_rows[0]) if no_cost_rows else None,
        "best_full": _candidate_payload(full_rows[0]) if full_rows else None,
        "best_passing": _candidate_payload(full_passing[0]) if full_passing else None,
        "best_near": _candidate_payload(full_rejected[0]) if full_rejected else None,
    }


def latest_by_query(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for row in rows:
        if row.get("type") != "query_diagnostic":
            continue
        query = str(row.get("query") or "")
        if query:
            latest[query] = row
    return sorted(latest.values(), key=lambda row: str(row.get("query") or ""))


def _matching_funnel(kalshi_markets: list[Market], polymarket_markets: list[Market], *, min_match_score: float) -> dict[str, object]:
    text_candidates = 0
    compatible = 0
    warnings: Counter[str] = Counter()
    rejected_examples: list[dict[str, object]] = []
    compatible_examples: list[dict[str, object]] = []
    for left in kalshi_markets:
        for right in polymarket_markets:
            details = market_match_details(left, right)
            if details.score < min_match_score:
                continue
            text_candidates += 1
            warnings.update(details.warnings)
            if not _has_structural_mismatch(details):
                compatible += 1
                if len(compatible_examples) < 5:
                    compatible_examples.append(_match_example(left, right, details))
            elif len(rejected_examples) < 8:
                rejected_examples.append(_match_example(left, right, details))
    return {
        "pairs_checked": len(kalshi_markets) * len(polymarket_markets),
        "text_candidates": text_candidates,
        "structurally_compatible_pairs": compatible,
        "warning_counts": dict(warnings.most_common(20)),
        "compatible_examples": compatible_examples,
        "rejected_examples": rejected_examples,
    }


def _match_example(left: Market, right: Market, details: object) -> dict[str, object]:
    return {
        "match_score": getattr(details, "score", None),
        "shared_tokens": list(getattr(details, "shared_tokens", []) or []),
        "warnings": list(getattr(details, "warnings", []) or []),
        "kalshi": _market_payload(left),
        "polymarket": _market_payload(right),
        "conditions": {
            "kalshi": _serializable(asdict(details.left_condition)) if getattr(details, "left_condition", None) else None,
            "polymarket": _serializable(asdict(details.right_condition)) if getattr(details, "right_condition", None) else None,
        },
    }


def _market_payload(market: Market) -> dict[str, object]:
    return {
        "market_id": market.market_id,
        "title": market.title,
        "close_time": market.close_time,
        "volume": market.volume,
        "liquidity": market.liquidity,
        "url": market.url,
    }


def _scan_summary(rows: list[DepthCandidate]) -> dict[str, object]:
    passing = [row for row in rows if not row.rejection_reason]
    return {
        "candidate_legs": len(rows),
        "passing_count": len(passing),
        "best_net_edge": rows[0].net_edge if rows else None,
        "best_profit": (rows[0].net_edge or 0.0) * rows[0].executable_size if rows and rows[0].net_edge is not None else None,
    }


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
