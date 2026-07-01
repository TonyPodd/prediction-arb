from __future__ import annotations

from datetime import datetime, timezone
import re

from prediction_arb.http import ApiError, get_json
from prediction_arb.models import Market, OrderBook, OrderLevel, TopOfBook


BASE_URL = "https://api.limitless.exchange"


def fetch_markets(limit: int = 100) -> list[Market]:
    markets: list[Market] = []
    page = 1
    page_size = 10

    while len(markets) < limit and page <= 20:
        data = get_json(
            f"{BASE_URL}/markets/active",
            {
                "page": page,
                "limit": page_size,
            },
        )
        rows = data.get("data", []) if isinstance(data, dict) else []
        if not rows:
            break
        markets.extend(_normalize_market(row) for row in rows if _is_supported_market(row))
        page += 1

    return markets[:limit]


def search_markets(query: str, limit: int = 100) -> list[Market]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return fetch_markets(limit=limit)

    rows = get_json(f"{BASE_URL}/markets/active/slugs")
    slugs = []
    for row in rows if isinstance(rows, list) else []:
        text = " ".join(str(row.get(key) or "") for key in ("slug", "ticker", "deadline"))
        if query_tokens <= _tokens(text):
            slug = row.get("slug")
            if slug:
                slugs.append(str(slug))

    markets = []
    for slug in slugs[:limit]:
        try:
            detail = get_json(f"{BASE_URL}/markets/{slug}")
        except ApiError:
            continue
        if isinstance(detail, dict) and _is_supported_market(detail):
            markets.append(_normalize_market(detail))
    return markets[:limit]


def fetch_orderbook(market: Market, outcome: str = "YES") -> OrderBook | None:
    data = get_json(f"{BASE_URL}/markets/{market.market_id}/orderbook")
    if not isinstance(data, dict):
        return None
    yes_book = OrderBook(
        source="limitless",
        token_id=str(data.get("tokenId") or market.raw.get("tokens", {}).get("yes") or market.market_id),
        outcome="YES",
        bids=_levels(data.get("bids", []), normalize_large_sizes=True),
        asks=_levels(data.get("asks", []), normalize_large_sizes=True),
    )
    if outcome == "YES":
        return yes_book
    return _no_book_from_yes_book(yes_book, market)


def _normalize_market(row: dict) -> Market:
    slug = str(row.get("slug") or row.get("conditionId") or row.get("id") or "")
    title = str(row.get("title") or row.get("proxyTitle") or slug)
    trade_prices = row.get("tradePrices") if isinstance(row.get("tradePrices"), dict) else {}
    buy_market = _array(trade_prices.get("buy", {}).get("market"))
    sell_market = _array(trade_prices.get("sell", {}).get("market"))
    prices = _array(row.get("prices"))

    yes_ask = _probability(_index(buy_market, 0))
    no_ask = _probability(_index(buy_market, 1))
    yes_bid = _probability(_index(sell_market, 0))
    no_bid = _probability(_index(sell_market, 1))

    if yes_ask is None:
        yes_ask = _probability(_index(prices, 0))
    if no_ask is None:
        no_ask = _probability(_index(prices, 1))
    if yes_bid is None and no_ask is not None:
        yes_bid = 1.0 - no_ask
    if no_bid is None and yes_ask is not None:
        no_bid = 1.0 - yes_ask

    return Market(
        source="limitless",
        market_id=slug,
        title=title,
        url=f"https://limitless.exchange/markets/{slug}" if slug else None,
        close_time=_close_time(row),
        volume=_float(row.get("volume") or row.get("volumeFormatted")),
        liquidity=None,
        top=TopOfBook(yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask),
        raw=row,
    )


def _is_supported_market(row: dict) -> bool:
    if row.get("hidden") or row.get("expired"):
        return False
    if row.get("tradeType") not in (None, "clob"):
        return False
    tokens = row.get("tokens")
    return isinstance(tokens, dict) and "yes" in tokens and "no" in tokens


def _close_time(row: dict) -> str | None:
    value = row.get("expirationTimestamp")
    if value:
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return str(value)
    return row.get("expirationDate")


def _array(value: object) -> list:
    return value if isinstance(value, list) else []


def _index(values: list, index: int) -> object | None:
    return values[index] if index < len(values) else None


def _probability(value: object) -> float | None:
    number = _float(value)
    if number is None or number <= 0 or number >= 1:
        return None
    return number


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _levels(rows: object, normalize_large_sizes: bool) -> list[OrderLevel]:
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
        if normalize_large_sizes and size > 1_000_000:
            size = size / 1_000_000.0
        levels.append(OrderLevel(price=price, size=size))
    return levels


def _no_book_from_yes_book(book: OrderBook, market: Market) -> OrderBook:
    token_id = str((market.raw.get("tokens") or {}).get("no") or f"{market.market_id}:NO")
    return OrderBook(
        source=book.source,
        token_id=token_id,
        outcome="NO",
        bids=[OrderLevel(price=1.0 - level.price, size=level.size) for level in book.asks if level.price < 1.0],
        asks=[OrderLevel(price=1.0 - level.price, size=level.size) for level in book.bids if level.price < 1.0],
    )


def _tokens(value: str) -> set[str]:
    aliases = {"bitcoin": "btc", "ethereum": "eth"}
    return {aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2}
