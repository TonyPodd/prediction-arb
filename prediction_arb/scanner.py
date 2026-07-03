from __future__ import annotations

from datetime import datetime, timezone

from prediction_arb.matching import MatchDetails, market_match_details
from prediction_arb.models import Market, Opportunity


def scan_opportunities(
    left_markets: list[Market],
    polymarket_markets: list[Market],
    min_edge: float = 0.02,
    min_match_score: float = 0.25,
) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    detected_at = datetime.now(tz=timezone.utc)

    for left in left_markets:
        for poly in polymarket_markets:
            details = market_match_details(left, poly)
            if details.score < min_match_score:
                continue
            if _has_structural_mismatch(details):
                continue

            opportunities.extend(_market_pair_opportunities(left, poly, details, detected_at, min_edge))

    return sorted(opportunities, key=lambda item: item.gross_edge, reverse=True)


def _has_structural_mismatch(details: MatchDetails) -> bool:
    hard_warnings = {
        "condition_kind_differs",
        "asset_differs",
        "direction_differs",
        "threshold_differs",
        "interval_differs",
        "deadline_differs",
        "outcome_subject_differs",
    }
    return bool(hard_warnings & set(details.warnings))


def _market_pair_opportunities(
    left: Market,
    right: Market,
    details: MatchDetails,
    detected_at: datetime,
    min_edge: float,
) -> list[Opportunity]:
    candidates = [
        ("YES", left, left.top.yes_ask, right, right.top.yes_bid),
        ("YES", right, right.top.yes_ask, left, left.top.yes_bid),
        ("NO", left, left.top.no_ask, right, right.top.no_bid),
        ("NO", right, right.top.no_ask, left, left.top.no_bid),
    ]

    opportunities = []
    for side, buy_market, buy_price, sell_market, sell_price in candidates:
        if buy_price is None or sell_price is None:
            continue
        edge = sell_price - buy_price
        if edge < min_edge:
            continue
        opportunities.append(
            Opportunity(
                side=side,
                buy_source=buy_market.source,
                buy_market_id=buy_market.market_id,
                buy_title=buy_market.title,
                buy_price=buy_price,
                sell_source=sell_market.source,
                sell_market_id=sell_market.market_id,
                sell_title=sell_market.title,
                sell_price=sell_price,
                gross_edge=edge,
                match_score=details.score,
                match_warnings=details.warnings,
                buy_condition_kind=details.left_condition_kind if buy_market is left else details.right_condition_kind,
                sell_condition_kind=details.right_condition_kind if sell_market is right else details.left_condition_kind,
                buy_url=buy_market.url,
                sell_url=sell_market.url,
                detected_at=detected_at,
            )
        )
    return opportunities
