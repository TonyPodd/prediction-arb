from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from dataclasses import asdict

from prediction_arb.matching import condition_from_market
from prediction_arb.models import Market


def summarize_source_coverage(
    limitless_markets: list[Market],
    polymarket_markets: list[Market],
    *,
    now: datetime | None = None,
    example_limit: int = 8,
) -> dict[str, object]:
    reference_time = now or datetime.now(tz=timezone.utc)
    return {
        "generated_at": reference_time.isoformat(),
        "sources": _source_summaries(limitless_markets, polymarket_markets, now=reference_time, example_limit=example_limit),
    }


def _source_summaries(
    left_markets: list[Market],
    right_markets: list[Market],
    *,
    now: datetime,
    example_limit: int,
) -> dict[str, object]:
    rows: dict[str, list[Market]] = {}
    for market in [*left_markets, *right_markets]:
        rows.setdefault(market.source, []).append(market)
    return {
        source: summarize_markets(markets, now=now, example_limit=example_limit)
        for source, markets in rows.items()
    }


def summarize_markets(markets: list[Market], *, now: datetime | None = None, example_limit: int = 8) -> dict[str, object]:
    reference_time = now or datetime.now(tz=timezone.utc)
    kinds: Counter[str] = Counter()
    assets: Counter[str] = Counter()
    intervals: Counter[str] = Counter()
    close_windows: Counter[str] = Counter()
    examples = []

    for market in markets:
        condition = condition_from_market(market)
        kinds[condition.kind] += 1
        assets[condition.asset or "unknown"] += 1
        intervals[str(condition.interval_minutes or "unknown")] += 1
        close_windows[_close_window(market.close_time, reference_time)] += 1
        if len(examples) < example_limit:
            examples.append(
                {
                    "market_id": market.market_id,
                    "title": market.title,
                    "close_time": market.close_time,
                    "volume": market.volume,
                    "liquidity": market.liquidity,
                    "condition": asdict(condition),
                    "url": market.url,
                }
            )

    return {
        "count": len(markets),
        "by_condition_kind": dict(sorted(kinds.items())),
        "by_asset": _top_counts(assets),
        "by_interval_minutes": dict(sorted(intervals.items(), key=_interval_sort_key)),
        "by_close_window": {key: close_windows.get(key, 0) for key in _CLOSE_WINDOW_ORDER},
        "short_term_24h_count": close_windows.get("0_1h", 0) + close_windows.get("1_6h", 0) + close_windows.get("6_24h", 0),
        "examples": examples,
    }


_CLOSE_WINDOW_ORDER = ["missing_or_invalid", "already_closed", "0_1h", "1_6h", "6_24h", "1_7d", "7d_plus"]


def _close_window(value: str | None, now: datetime) -> str:
    close_at = _parse_datetime(value)
    if close_at is None:
        return "missing_or_invalid"
    hours = (close_at - now).total_seconds() / 3600.0
    if hours < 0:
        return "already_closed"
    if hours <= 1:
        return "0_1h"
    if hours <= 6:
        return "1_6h"
    if hours <= 24:
        return "6_24h"
    if hours <= 24 * 7:
        return "1_7d"
    return "7d_plus"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _top_counts(counter: Counter[str], limit: int = 12) -> dict[str, int]:
    return dict(counter.most_common(limit))


def _interval_sort_key(item: tuple[str, int]) -> tuple[int, int | str]:
    key, _count = item
    if key == "unknown":
        return (1, key)
    return (0, int(key))
