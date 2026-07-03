from __future__ import annotations

import re
from datetime import datetime, timezone

from prediction_arb.http import get_json
from prediction_arb.models import Market, OrderBook, OrderLevel, TopOfBook


BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


def fetch_markets(limit: int = 100) -> list[Market]:
    markets: list[Market] = []
    cursor: str | None = None
    page_size = min(max(limit, 1), 100)

    while len(markets) < limit:
        params = {
            "status": "open",
            "limit": page_size,
            "mve_filter": "exclude",
        }
        if cursor:
            params["cursor"] = cursor
        data = get_json(f"{BASE_URL}/markets", params)
        rows = data.get("markets", []) if isinstance(data, dict) else []
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict) or not _is_supported_market(row):
                continue
            markets.append(_normalize_market(row))
            if len(markets) >= limit:
                break
        cursor = data.get("cursor") if isinstance(data, dict) else None
        if not cursor:
            break

    return markets[:limit]


def search_markets(query: str, limit: int = 100) -> list[Market]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return fetch_markets(limit=limit)
    markets: list[Market] = []
    seen: set[str] = set()
    for tag in _query_tags(query_tokens):
        _extend_unique(markets, seen, _fetch_markets_for_tag(tag, limit=limit), limit=limit)
        if len(markets) >= limit:
            return markets[:limit]
    fallback = [
        market
        for market in fetch_markets(limit=max(limit, 500))
        if query_tokens <= _tokens(_search_text(market))
    ]
    _extend_unique(markets, seen, fallback, limit=limit)
    return markets[:limit]


def _fetch_markets_for_tag(tag: str, limit: int) -> list[Market]:
    series_rows = get_json(
        f"{BASE_URL}/series",
        {
            "category": "Crypto",
            "tags": tag,
            "include_volume": "true",
        },
    )
    series = series_rows.get("series", []) if isinstance(series_rows, dict) else []
    markets: list[Market] = []
    seen: set[str] = set()
    for row in series if isinstance(series, list) else []:
        ticker = row.get("ticker") if isinstance(row, dict) else None
        if not ticker:
            continue
        _extend_unique(markets, seen, _fetch_series_markets(str(ticker), limit=limit), limit=limit)
        if len(markets) >= limit:
            break
    return markets[:limit]


def _fetch_series_markets(series_ticker: str, limit: int) -> list[Market]:
    markets: list[Market] = []
    cursor: str | None = None
    page_size = min(max(limit, 1), 100)
    while len(markets) < limit:
        params = {
            "status": "open",
            "limit": page_size,
            "mve_filter": "exclude",
            "series_ticker": series_ticker,
        }
        if cursor:
            params["cursor"] = cursor
        data = get_json(f"{BASE_URL}/markets", params)
        rows = data.get("markets", []) if isinstance(data, dict) else []
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict) or not _is_supported_market(row):
                continue
            markets.append(_normalize_market(row))
            if len(markets) >= limit:
                break
        cursor = data.get("cursor") if isinstance(data, dict) else None
        if not cursor:
            break
    return markets[:limit]


def fetch_orderbook(market: Market, outcome: str = "YES") -> OrderBook | None:
    data = get_json(f"{BASE_URL}/markets/{market.market_id}/orderbook", {"depth": 100})
    book = data.get("orderbook_fp") if isinstance(data, dict) else None
    if not isinstance(book, dict):
        return None
    yes_bids = _levels(book.get("yes_dollars", []))
    no_bids = _levels(book.get("no_dollars", []))
    if outcome == "YES":
        return OrderBook(
            source="kalshi",
            token_id=market.market_id,
            outcome="YES",
            bids=yes_bids,
            asks=_complement_asks(no_bids),
        )
    return OrderBook(
        source="kalshi",
        token_id=market.market_id,
        outcome="NO",
        bids=no_bids,
        asks=_complement_asks(yes_bids),
    )


def _normalize_market(row: dict) -> Market:
    ticker = str(row.get("ticker") or "")
    title = str(row.get("title") or row.get("yes_sub_title") or ticker)
    yes_bid = _probability(row.get("yes_bid_dollars"))
    yes_ask = _probability(row.get("yes_ask_dollars"))
    no_bid = _probability(row.get("no_bid_dollars"))
    no_ask = _probability(row.get("no_ask_dollars"))
    return Market(
        source="kalshi",
        market_id=ticker,
        title=title,
        url=f"https://kalshi.com/markets/{ticker.lower()}" if ticker else None,
        close_time=row.get("close_time") or row.get("expected_expiration_time") or row.get("expiration_time"),
        volume=_float(row.get("volume_24h_fp") or row.get("volume_fp")),
        liquidity=_float(row.get("liquidity_dollars")),
        top=TopOfBook(yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask),
        raw=row,
    )


def _is_supported_market(row: dict) -> bool:
    if row.get("market_type") not in (None, "binary"):
        return False
    if row.get("status") not in (None, "active", "open"):
        return False
    if row.get("is_provisional") is True:
        return False
    if str(row.get("ticker") or "").startswith("KXMVE"):
        return False
    return bool(row.get("ticker"))


def _levels(rows: object) -> list[OrderLevel]:
    if not isinstance(rows, list):
        return []
    levels = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price = _probability(row[0])
        size = _float(row[1])
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        levels.append(OrderLevel(price=price, size=size))
    return levels


def _complement_asks(bids: list[OrderLevel]) -> list[OrderLevel]:
    return [OrderLevel(price=1.0 - level.price, size=level.size) for level in bids if 0 < level.price < 1.0]


def _extend_unique(markets: list[Market], seen: set[str], rows: list[Market], *, limit: int) -> None:
    for market in rows:
        key = market.market_id or market.title
        if key in seen:
            continue
        seen.add(key)
        markets.append(market)
        if len(markets) >= limit:
            break


def _query_tags(tokens: set[str]) -> list[str]:
    mapping = {
        "btc": "BTC",
        "eth": "ETH",
        "sol": "SOL",
        "doge": "DOGE",
        "bnb": "BNB",
        "hype": "HYPE",
        "xrp": "XRP",
        "zec": "ZEC",
        "near": "NEAR",
    }
    return [tag for token, tag in mapping.items() if token in tokens]


def _probability(value: object) -> float | None:
    number = _float(value)
    if number is None or number < 0 or number > 1:
        return None
    return number


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _search_text(market: Market) -> str:
    raw = market.raw or {}
    return " ".join(
        str(item)
        for item in [
            market.title,
            raw.get("yes_sub_title", ""),
            raw.get("no_sub_title", ""),
            raw.get("event_ticker", ""),
            raw.get("series_ticker", ""),
            raw.get("category", ""),
            " ".join(str(tag) for tag in raw.get("tags", []) if tag),
        ]
        if item
    )


def _tokens(value: str) -> set[str]:
    aliases = {"bitcoin": "btc", "ethereum": "eth", "solana": "sol"}
    return {aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2}
