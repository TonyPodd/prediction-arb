from __future__ import annotations

from datetime import datetime, timezone

from prediction_arb.depth import scan_depth_candidates
from prediction_arb.models import DepthCandidate, Market, MonitorSnapshot


def monitor_once(
    query: str,
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    previous_keys: set[str],
    size: float,
    min_net_edge: float = 0.005,
    safety_buffer: float = 0.002,
    min_match_score: float = 0.25,
    fee_bps: float = 0.0,
) -> tuple[MonitorSnapshot, set[str]]:
    opportunities = scan_depth_candidates(
        limitless_markets=limitless_markets,
        polymarket_markets=polymarket_markets,
        size=size,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        min_match_score=min_match_score,
        allow_partial=False,
        fee_bps=fee_bps,
        include_filtered=False,
    )
    active_keys = {_opportunity_key(item) for item in opportunities}
    new_keys = sorted(active_keys - previous_keys)
    gone_keys = sorted(previous_keys - active_keys)
    snapshot = MonitorSnapshot(
        query=query,
        size=size,
        detected_at=datetime.now(tz=timezone.utc),
        opportunity_count=len(opportunities),
        new_count=len(new_keys),
        gone_count=len(gone_keys),
        active_keys=sorted(active_keys),
        new_keys=new_keys,
        gone_keys=gone_keys,
        opportunities=opportunities,
    )
    return snapshot, active_keys


def _opportunity_key(candidate: DepthCandidate) -> str:
    return "|".join(
        [
            candidate.outcome,
            candidate.buy_source,
            candidate.buy_market_id,
            candidate.sell_source,
            candidate.sell_market_id,
        ]
    )
