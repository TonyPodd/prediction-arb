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
    min_profit: float = 0.0,
    route_fixed_costs: dict[str, float] | None = None,
    route_cost_bps: dict[str, float] | None = None,
    max_depth_pairs: int = 0,
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
        min_profit=min_profit,
        route_fixed_costs=route_fixed_costs,
        route_cost_bps=route_cost_bps,
        include_filtered=False,
        max_depth_pairs=max_depth_pairs,
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


def format_new_opportunity_alert(snapshot: MonitorSnapshot, max_items: int = 5) -> str | None:
    if not snapshot.new_keys:
        return None

    opportunities_by_key = {_opportunity_key(item): item for item in snapshot.opportunities}
    lines = [
        f"New prediction-arb opportunities for '{snapshot.query}': {snapshot.new_count}",
        f"size={snapshot.size:g} detected_at={snapshot.detected_at.isoformat()}",
    ]
    for key in snapshot.new_keys[:max_items]:
        item = opportunities_by_key.get(key)
        if item is None:
            lines.append(f"- {key}")
            continue
        net_edge = item.net_edge if item.net_edge is not None else 0.0
        profit = net_edge * item.executable_size
        lines.append(
            "- "
            f"{item.outcome} {item.buy_source}->{item.sell_source} "
            f"net_edge={net_edge:.4f} size={item.executable_size:g} est_profit={profit:.4f}"
        )
    remaining = len(snapshot.new_keys) - max_items
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    return "\n".join(lines)


def build_webhook_payload(text: str, webhook_format: str) -> dict[str, str]:
    if webhook_format == "discord":
        return {"content": text}
    return {"text": text}


def build_telegram_payload(chat_id: str, text: str, reply_markup: dict[str, object] | None = None) -> dict[str, object]:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return payload
