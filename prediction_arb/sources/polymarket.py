from __future__ import annotations

import json

from prediction_arb.http import get_json
from prediction_arb.models import Market, OrderBook, OrderLevel, TopOfBook


GAMMA_URL = "https://gamma-api.polymarket.com"


def fetch_markets(limit: int = 100) -> list[Market]:
    return _fetch_market_feed(limit=limit, order="volume24hr")


def fetch_markets_expanded(limit: int = 100) -> list[Market]:
    markets: list[Market] = []
    seen: set[str] = set()
    per_feed_limit = max(limit, 200)

    for order in ("volume24hr", "volume_24hr", "liquidity", "endDate"):
        _extend_unique(markets, seen, _fetch_market_feed(limit=per_feed_limit, order=order), limit=limit)
        if len(markets) >= limit:
            return markets[:limit]

    for order in ("volume_24hr", "liquidity", "end_date"):
        _extend_unique(markets, seen, _fetch_event_markets(limit=per_feed_limit, order=order), limit=limit)
        if len(markets) >= limit:
            return markets[:limit]

    return markets[:limit]


def _fetch_market_feed(limit: int, order: str) -> list[Market]:
    markets: list[Market] = []
    seen: set[str] = set()
    offset = 0
    page_size = 100

    while len(markets) < limit and offset < 10_000:
        current_limit = min(page_size, limit - len(markets))
        data = get_json(
            f"{GAMMA_URL}/markets",
            {
                "active": "true",
                "closed": "false",
                "limit": current_limit,
                "offset": offset,
                "order": order,
                "ascending": "false",
            },
        )
        rows = data if isinstance(data, list) else data.get("markets", [])
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            market = _normalize_market(row)
            key = market.market_id or market.title
            if key in seen:
                continue
            seen.add(key)
            markets.append(market)
            if len(markets) >= limit:
                break
        offset += len(rows)
        if len(rows) < current_limit:
            break

    return markets[:limit]


def _fetch_event_markets(limit: int, order: str) -> list[Market]:
    markets: list[Market] = []
    seen: set[str] = set()
    offset = 0
    page_size = 100

    while len(markets) < limit and offset < 10_000:
        current_limit = min(page_size, limit - len(markets))
        data = get_json(
            f"{GAMMA_URL}/events",
            {
                "active": "true",
                "closed": "false",
                "limit": current_limit,
                "offset": offset,
                "order": order,
                "ascending": "false",
            },
        )
        rows = data if isinstance(data, list) else data.get("events", [])
        if not rows:
            break
        for event in rows:
            if not isinstance(event, dict):
                continue
            for row in event.get("markets", []) if isinstance(event.get("markets"), list) else []:
                if not isinstance(row, dict) or not _is_active_market_row(row):
                    continue
                enriched = dict(row)
                enriched["events"] = [event]
                enriched["eventSlug"] = event.get("slug")
                enriched["eventTitle"] = event.get("title")
                market = _normalize_market(enriched)
                key = market.market_id or market.title
                if key in seen:
                    continue
                seen.add(key)
                markets.append(market)
                if len(markets) >= limit:
                    break
            if len(markets) >= limit:
                break
        offset += len(rows)
        if len(rows) < current_limit:
            break

    return markets[:limit]


def search_markets(query: str, limit: int = 100) -> list[Market]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return fetch_markets(limit=limit)
    markets: list[Market] = []
    seen: set[str] = set()
    local_matches = [
        market
        for market in fetch_markets_expanded(limit=max(limit, 500))
        if query_tokens <= _tokens(_search_text(market))
    ]
    _extend_unique(markets, seen, local_matches, limit=limit)
    if len(markets) < limit:
        _extend_unique(markets, seen, fetch_public_search_markets(query, limit=max(limit, 100)), limit=limit)
    return markets[:limit]


def fetch_public_search_markets(query: str, limit: int = 100) -> list[Market]:
    data = get_json(
        f"{GAMMA_URL}/public-search",
        {
            "q": query,
            "limit": limit,
        },
    )
    events = data.get("events", []) if isinstance(data, dict) else []
    markets: list[Market] = []
    seen: set[str] = set()
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        for row in event.get("markets", []) if isinstance(event.get("markets"), list) else []:
            if not isinstance(row, dict) or not _is_active_market_row(row):
                continue
            enriched = dict(row)
            enriched["events"] = [event]
            enriched["eventSlug"] = event.get("slug")
            enriched["eventTitle"] = event.get("title")
            market = _normalize_market(enriched)
            key = market.market_id or market.title
            if key in seen:
                continue
            seen.add(key)
            markets.append(market)
            if len(markets) >= limit:
                return markets
    return markets


def fetch_orderbook(market: Market, outcome: str) -> OrderBook | None:
    token_id = token_id_for_outcome(market, outcome)
    if not token_id:
        return None
    data = get_json("https://clob.polymarket.com/book", {"token_id": token_id})
    if not isinstance(data, dict):
        return None
    return OrderBook(
        source="polymarket",
        token_id=token_id,
        outcome=outcome,
        bids=_levels(data.get("bids", [])),
        asks=_levels(data.get("asks", [])),
    )


def token_id_for_outcome(market: Market, outcome: str) -> str | None:
    tokens = _parse_array(market.raw.get("clobTokenIds"))
    index = 0 if outcome == "YES" else 1
    if index >= len(tokens):
        return None
    token = tokens[index]
    return str(token) if token else None


def _normalize_market(row: dict) -> Market:
    market_id = str(row.get("id") or row.get("conditionId") or row.get("slug") or "")
    slug = row.get("slug")
    title = str(row.get("question") or row.get("title") or row.get("description") or market_id)

    prices = _parse_array(row.get("outcomePrices"))
    outcomes = [str(item).lower() for item in _parse_array(row.get("outcomes"))]
    yes_price = _price_for_outcome(outcomes, prices, "yes")
    no_price = _price_for_outcome(outcomes, prices, "no")

    best_bid = _probability(row.get("bestBid") or row.get("oneDayPriceChange"))
    best_ask = _probability(row.get("bestAsk"))
    yes_bid = _probability(row.get("yesBid")) or best_bid
    yes_ask = _probability(row.get("yesAsk")) or best_ask or yes_price
    no_bid = _probability(row.get("noBid")) or no_price
    no_ask = _probability(row.get("noAsk"))

    if yes_ask is None and no_bid is not None:
        yes_ask = 1.0 - no_bid
    if no_ask is None and yes_bid is not None:
        no_ask = 1.0 - yes_bid

    return Market(
        source="polymarket",
        market_id=market_id,
        title=title,
        url=f"https://polymarket.com/event/{row.get('eventSlug') or slug}" if (row.get("eventSlug") or slug) else None,
        close_time=row.get("endDate") or row.get("endDateIso") or row.get("closeTime"),
        volume=_float(row.get("volume") or row.get("volume24hr")),
        liquidity=_float(row.get("liquidity")),
        top=TopOfBook(yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask),
        raw=row,
    )


def _extend_unique(markets: list[Market], seen: set[str], rows: list[Market], *, limit: int) -> None:
    for market in rows:
        key = market.market_id or market.title
        if key in seen:
            continue
        seen.add(key)
        markets.append(market)
        if len(markets) >= limit:
            break


def _is_active_market_row(row: dict) -> bool:
    if row.get("closed") is True or row.get("archived") is True:
        return False
    if row.get("active") is False:
        return False
    if row.get("enableOrderBook") is False:
        return False
    return True


def _parse_array(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _price_for_outcome(outcomes: list[str], prices: list, outcome: str) -> float | None:
    try:
        index = outcomes.index(outcome)
    except ValueError:
        return None
    if index >= len(prices):
        return None
    return _probability(prices[index])


def _probability(value: object) -> float | None:
    number = _float(value)
    if number is None:
        return None
    if number > 1:
        number = number / 100.0
    if number < 0 or number > 1:
        return None
    return number


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _levels(rows: object) -> list[OrderLevel]:
    if not isinstance(rows, list):
        return []
    levels = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _float(row.get("price"))
        size = _float(row.get("size"))
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        levels.append(OrderLevel(price=price, size=size))
    return levels


def _search_text(market: Market) -> str:
    raw = market.raw or {}
    return " ".join(
        str(item)
        for item in [
            market.title,
            raw.get("description", ""),
            raw.get("slug", ""),
            raw.get("groupItemTitle", ""),
            *(event.get("title", "") for event in raw.get("events", []) if isinstance(event, dict)),
        ]
        if item
    )


def _tokens(value: str) -> set[str]:
    aliases = {"bitcoin": "btc", "ethereum": "eth"}
    import re

    return {aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2}
