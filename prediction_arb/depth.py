from __future__ import annotations

from datetime import datetime, timezone

from prediction_arb.matching import MatchDetails, market_match_details
from prediction_arb.models import DepthCandidate, DepthMaxResult, DepthOpportunity, DepthSweepRow, ExecutionQuote, Market, OrderBook, OrderLevel
from prediction_arb.scanner import _has_structural_mismatch
from prediction_arb.sources import limitless, polymarket


def quote_execution(
    book: OrderBook,
    side: str,
    requested_size: float,
    allow_partial: bool = False,
) -> ExecutionQuote:
    levels = _sorted_levels(book.asks, reverse=False) if side == "BUY" else _sorted_levels(book.bids, reverse=True)
    remaining = requested_size
    filled = 0.0
    notional = 0.0
    worst_price = None

    for level in levels:
        if remaining <= 0:
            break
        fill = min(remaining, level.size)
        if fill <= 0:
            continue
        filled += fill
        remaining -= fill
        notional += fill * level.price
        worst_price = level.price

    complete = filled >= requested_size
    if not complete and not allow_partial:
        return ExecutionQuote(
            side=side,
            outcome=book.outcome,
            requested_size=requested_size,
            filled_size=filled,
            avg_price=None,
            worst_price=worst_price,
            notional=notional,
            complete=False,
        )

    avg_price = notional / filled if filled > 0 else None
    return ExecutionQuote(
        side=side,
        outcome=book.outcome,
        requested_size=requested_size,
        filled_size=filled,
        avg_price=avg_price,
        worst_price=worst_price,
        notional=notional,
        complete=complete,
    )


def scan_depth_opportunities(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    size: float,
    min_net_edge: float = 0.005,
    safety_buffer: float = 0.002,
    min_match_score: float = 0.25,
    allow_partial: bool = False,
    fee_bps: float = 0.0,
    min_profit: float = 0.0,
    route_fixed_costs: dict[str, float] | None = None,
    route_cost_bps: dict[str, float] | None = None,
) -> list[DepthOpportunity]:
    rows = scan_depth_candidates(
        limitless_markets=limitless_markets,
        polymarket_markets=polymarket_markets,
        size=size,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        min_match_score=min_match_score,
        allow_partial=allow_partial,
        fee_bps=fee_bps,
        min_profit=min_profit,
        route_fixed_costs=route_fixed_costs,
        route_cost_bps=route_cost_bps,
        include_filtered=False,
    )
    return [
        DepthOpportunity(
            outcome=row.outcome,
            buy_source=row.buy_source,
            buy_market_id=row.buy_market_id,
            buy_title=row.buy_title,
            sell_source=row.sell_source,
            sell_market_id=row.sell_market_id,
            sell_title=row.sell_title,
            top_of_book_edge=row.top_of_book_edge,
            depth_edge=row.depth_edge if row.depth_edge is not None else 0.0,
            net_edge=row.net_edge if row.net_edge is not None else 0.0,
            safety_buffer=row.safety_buffer,
            fee_estimate=row.fee_estimate,
            rejection_reason=row.rejection_reason,
            executable_size=row.executable_size,
            buy_quote=row.buy_quote,
            sell_quote=row.sell_quote,
            match_score=row.match_score,
            match_warnings=row.match_warnings,
            buy_url=row.buy_url,
            sell_url=row.sell_url,
            detected_at=row.detected_at,
        )
        for row in rows
    ]


def scan_depth_candidates(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    size: float,
    min_net_edge: float = 0.005,
    safety_buffer: float = 0.002,
    min_match_score: float = 0.25,
    allow_partial: bool = False,
    fee_bps: float = 0.0,
    min_profit: float = 0.0,
    route_fixed_costs: dict[str, float] | None = None,
    route_cost_bps: dict[str, float] | None = None,
    include_filtered: bool = False,
) -> list[DepthCandidate]:
    detected_at = datetime.now(tz=timezone.utc)
    rows: list[DepthCandidate] = []
    book_cache: dict[tuple[str, str, str], OrderBook | None] = {}

    for left in limitless_markets:
        for right in polymarket_markets:
            details = market_match_details(left, right)
            if details.score < min_match_score or _has_structural_mismatch(details):
                continue
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
                        allow_partial,
                        fee_bps,
                        min_profit,
                        route_fixed_costs or {},
                        route_cost_bps or {},
                        include_filtered,
                        detected_at,
                        book_cache,
                    )
                )

    return sorted(rows, key=lambda item: item.net_edge if item.net_edge is not None else -999.0, reverse=True)


def sweep_depth(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    sizes: list[float],
    min_net_edge: float = 0.005,
    safety_buffer: float = 0.002,
    min_match_score: float = 0.25,
    fee_bps: float = 0.0,
    min_profit: float = 0.0,
    route_fixed_costs: dict[str, float] | None = None,
    route_cost_bps: dict[str, float] | None = None,
) -> list[DepthSweepRow]:
    rows = []
    book_cache: dict[tuple[str, str, str], OrderBook | None] = {}
    for size in sizes:
        opportunities = _scan_depth_candidates_with_cache(
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
            book_cache=book_cache,
        )
        best = opportunities[0] if opportunities else None
        rows.append(
            DepthSweepRow(
                size=size,
                opportunities=opportunities,
                best_net_edge=best.net_edge if best else None,
                best_net_profit=(best.net_edge * best.executable_size) if best and best.net_edge is not None else None,
                best_outcome=best.outcome if best else None,
                best_route=f"{best.buy_source}->{best.sell_source}" if best else None,
            )
        )
    return rows


def _scan_depth_candidates_with_cache(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    size: float,
    min_net_edge: float,
    safety_buffer: float,
    min_match_score: float,
    allow_partial: bool,
    fee_bps: float,
    min_profit: float,
    route_fixed_costs: dict[str, float] | None,
    route_cost_bps: dict[str, float] | None,
    include_filtered: bool,
    book_cache: dict[tuple[str, str, str], OrderBook | None],
) -> list[DepthCandidate]:
    detected_at = datetime.now(tz=timezone.utc)
    rows: list[DepthCandidate] = []
    for left in limitless_markets:
        for right in polymarket_markets:
            details = market_match_details(left, right)
            if details.score < min_match_score or _has_structural_mismatch(details):
                continue
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
                        allow_partial,
                        fee_bps,
                        min_profit,
                        route_fixed_costs or {},
                        route_cost_bps or {},
                        include_filtered,
                        detected_at,
                        book_cache,
                    )
                )
    return sorted(rows, key=lambda item: item.net_edge if item.net_edge is not None else -999.0, reverse=True)


def find_max_depth_size(
    query: str,
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    min_size: float,
    max_size: float,
    step_multiplier: float,
    min_net_edge: float = 0.005,
    safety_buffer: float = 0.002,
    min_match_score: float = 0.25,
    fee_bps: float = 0.0,
    min_profit: float = 0.0,
    route_fixed_costs: dict[str, float] | None = None,
    route_cost_bps: dict[str, float] | None = None,
) -> DepthMaxResult:
    sizes = _geometric_sizes(min_size, max_size, step_multiplier)
    checked = sweep_depth(
        limitless_markets=limitless_markets,
        polymarket_markets=polymarket_markets,
        sizes=sizes,
        min_net_edge=min_net_edge,
        safety_buffer=safety_buffer,
        min_match_score=min_match_score,
        fee_bps=fee_bps,
        min_profit=min_profit,
        route_fixed_costs=route_fixed_costs,
        route_cost_bps=route_cost_bps,
    )
    passing = [row for row in checked if row.opportunities]
    best_row = passing[-1] if passing else None
    return DepthMaxResult(
        query=query,
        min_size=min_size,
        max_size=max_size,
        step_multiplier=step_multiplier,
        max_passing_size=best_row.size if best_row else None,
        best_at_max_size=best_row.opportunities[0] if best_row else None,
        checked_sizes=checked,
    )


def _pair_depth_opportunities(
    left: Market,
    right: Market,
    details: MatchDetails,
    outcome: str,
    size: float,
    min_net_edge: float,
    safety_buffer: float,
    allow_partial: bool,
    fee_bps: float,
    min_profit: float,
    route_fixed_costs: dict[str, float],
    route_cost_bps: dict[str, float],
    include_filtered: bool,
    detected_at: datetime,
    book_cache: dict[tuple[str, str, str], OrderBook | None],
) -> list[DepthCandidate]:
    left_book = _fetch_book_cached(left, outcome, book_cache)
    right_book = _fetch_book_cached(right, outcome, book_cache)
    if left_book is None or right_book is None:
        return []

    candidates = [
        (left, left_book, right, right_book),
        (right, right_book, left, left_book),
    ]
    rows = []
    for buy_market, buy_book, sell_market, sell_book in candidates:
        buy_quote = quote_execution(buy_book, "BUY", size, allow_partial=allow_partial)
        sell_quote = quote_execution(sell_book, "SELL", size, allow_partial=allow_partial)
        rejection_reason = None
        if not allow_partial and (not buy_quote.complete or not sell_quote.complete):
            rejection_reason = "incomplete_fill"
        elif not buy_quote.avg_price or not sell_quote.avg_price:
            rejection_reason = "missing_executable_price"
        executable_size = min(buy_quote.filled_size, sell_quote.filled_size)
        if rejection_reason is None and executable_size <= 0:
            rejection_reason = "zero_executable_size"

        depth_edge = None
        fee_estimate = None
        net_edge = None
        if rejection_reason is None and buy_quote.avg_price is not None and sell_quote.avg_price is not None:
            depth_edge = sell_quote.avg_price - buy_quote.avg_price
            fee_estimate, fee_notes = _fee_estimate_per_share(
                buy_market,
                sell_market,
                buy_quote.avg_price,
                sell_quote.avg_price,
                fee_bps,
                executable_size,
                route_fixed_costs,
                route_cost_bps,
            )
            net_edge = depth_edge - safety_buffer - fee_estimate
            if net_edge < min_net_edge:
                rejection_reason = "net_edge_below_threshold"
            elif net_edge * executable_size < min_profit:
                rejection_reason = "profit_below_threshold"
        else:
            fee_notes = []

        if rejection_reason and not include_filtered:
            continue

        rows.append(
            DepthCandidate(
                outcome=outcome,
                buy_source=buy_market.source,
                buy_market_id=buy_market.market_id,
                buy_title=buy_market.title,
                sell_source=sell_market.source,
                sell_market_id=sell_market.market_id,
                sell_title=sell_market.title,
                top_of_book_edge=_top_edge(buy_market, sell_market, outcome),
                depth_edge=depth_edge,
                net_edge=net_edge,
                safety_buffer=safety_buffer,
                fee_estimate=fee_estimate,
                fee_notes=fee_notes,
                rejection_reason=rejection_reason,
                executable_size=executable_size,
                buy_quote=buy_quote,
                sell_quote=sell_quote,
                match_score=details.score,
                match_warnings=details.warnings,
                buy_url=buy_market.url,
                sell_url=sell_market.url,
                detected_at=detected_at,
            )
        )
    return rows


def _fee_estimate_per_share(
    buy_market: Market,
    sell_market: Market,
    buy_avg: float,
    sell_avg: float,
    manual_fee_bps: float,
    executable_size: float = 0.0,
    route_fixed_costs: dict[str, float] | None = None,
    route_cost_bps: dict[str, float] | None = None,
) -> tuple[float, list[str]]:
    buy_fee, buy_notes = _market_taker_fee_per_share(buy_market, buy_avg)
    sell_fee, sell_notes = _market_taker_fee_per_share(sell_market, sell_avg)
    manual_fee = (buy_avg + sell_avg) * (manual_fee_bps / 10_000.0) if manual_fee_bps > 0 else 0.0
    notes = buy_notes + sell_notes
    if manual_fee_bps > 0:
        notes.append(f"manual_fee_bps={manual_fee_bps}")
    elif any("unknown" in note or "no_fee_field" in note or "manual_fee_bps" in note for note in notes):
        notes.append("manual_fee_buffer_missing")
    route_fee, route_notes = _route_operational_cost_per_share(
        buy_market,
        sell_market,
        buy_avg,
        sell_avg,
        executable_size,
        route_fixed_costs or {},
        route_cost_bps or {},
    )
    return buy_fee + sell_fee + manual_fee + route_fee, notes + route_notes


def _route_operational_cost_per_share(
    buy_market: Market,
    sell_market: Market,
    buy_avg: float,
    sell_avg: float,
    executable_size: float,
    route_fixed_costs: dict[str, float],
    route_cost_bps: dict[str, float],
) -> tuple[float, list[str]]:
    route = f"{buy_market.source}->{sell_market.source}"
    notes = []
    fixed = _route_cost(route_fixed_costs, route)
    bps = _route_cost(route_cost_bps, route)
    fee = 0.0
    if fixed > 0:
        if executable_size > 0:
            fee += fixed / executable_size
            notes.append(f"route_fixed_cost_usdc={route}:{fixed}")
        else:
            notes.append(f"route_fixed_cost_unallocated={route}:{fixed}")
    if bps > 0:
        fee += (buy_avg + sell_avg) * (bps / 10_000.0)
        notes.append(f"route_cost_bps={route}:{bps}")
    return fee, notes


def _route_cost(costs: dict[str, float], route: str) -> float:
    return float(costs.get(route, costs.get("*", 0.0)) or 0.0)


def _market_taker_fee_per_share(market: Market, price: float) -> tuple[float, list[str]]:
    if market.source == "polymarket":
        if market.raw.get("feesEnabled") is False:
            return 0.0, ["polymarket_fees_disabled"]
        schedule = market.raw.get("feeSchedule") if isinstance(market.raw.get("feeSchedule"), dict) else {}
        rate = _float(schedule.get("rate"))
        if rate is None:
            taker_base_fee = _float(market.raw.get("takerBaseFee"))
            rate = taker_base_fee / 10_000.0 if taker_base_fee else 0.06
        exponent = _float(schedule.get("exponent")) or 1.0
        fee = _round_fee(rate * (price * (1.0 - price)) ** exponent)
        return fee, [f"polymarket_fee_rate={rate}", f"polymarket_fee_exponent={exponent}", "polymarket_fee_rounded_5dp"]

    if market.source == "limitless":
        creator_fee_pct = _float((market.raw.get("settings") or {}).get("creatorFeePct"))
        if creator_fee_pct:
            return price * (creator_fee_pct / 100.0), [f"limitless_creator_fee_pct={creator_fee_pct}"]
        if (market.raw.get("metadata") or {}).get("fee") is True:
            return 0.0, ["limitless_fee_curve_unknown_use_manual_fee_bps"]
        return 0.0, ["limitless_no_fee_field"]

    return 0.0, [f"{market.source}_fee_unknown"]


def estimate_market_taker_fee_per_share(market: Market, price: float) -> tuple[float, list[str]]:
    return _market_taker_fee_per_share(market, price)


def _round_fee(value: float) -> float:
    if value <= 0:
        return 0.0
    rounded = round(value, 5)
    return rounded if rounded >= 0.00001 else 0.0


def _geometric_sizes(min_size: float, max_size: float, step_multiplier: float) -> list[float]:
    if min_size <= 0:
        raise ValueError("min_size must be positive")
    if max_size < min_size:
        raise ValueError("max_size must be >= min_size")
    if step_multiplier <= 1:
        raise ValueError("step_multiplier must be > 1")

    sizes = []
    current = min_size
    while current < max_size:
        sizes.append(float(current))
        current *= step_multiplier
    if not sizes or sizes[-1] != max_size:
        sizes.append(float(max_size))
    return sizes


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_book(market: Market, outcome: str) -> OrderBook | None:
    if market.source == "limitless":
        return limitless.fetch_orderbook(market, outcome)
    if market.source == "polymarket":
        return polymarket.fetch_orderbook(market, outcome)
    return None


def _fetch_book_cached(
    market: Market,
    outcome: str,
    book_cache: dict[tuple[str, str, str], OrderBook | None],
) -> OrderBook | None:
    key = (market.source, market.market_id, outcome)
    if key not in book_cache:
        book_cache[key] = _fetch_book(market, outcome)
    return book_cache[key]


def _sorted_levels(levels: list[OrderLevel], reverse: bool) -> list[OrderLevel]:
    return sorted(levels, key=lambda item: item.price, reverse=reverse)


def _top_edge(buy_market: Market, sell_market: Market, outcome: str) -> float | None:
    buy_price = buy_market.top.yes_ask if outcome == "YES" else buy_market.top.no_ask
    sell_price = sell_market.top.yes_bid if outcome == "YES" else sell_market.top.no_bid
    if buy_price is None or sell_price is None:
        return None
    return sell_price - buy_price
