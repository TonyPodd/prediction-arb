from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from prediction_arb.models import Market, TopOfBook


DEFAULT_CACHE_DIR = Path("data/cache")


def cached_markets(key: str, ttl_seconds: float, fetcher: Callable[[], list[Market]], cache_dir: Path = DEFAULT_CACHE_DIR) -> list[Market]:
    path = cache_dir / f"{_safe_key(key)}.json"
    now = time.time()
    if ttl_seconds > 0 and path.exists() and now - path.stat().st_mtime <= ttl_seconds:
        rows = _read_market_cache(path)
        if rows is not None:
            return rows
    rows = fetcher()
    if ttl_seconds > 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cached_at": now, "markets": [asdict(row) for row in rows]}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return rows


def _read_market_cache(path: Path) -> list[Market] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = payload.get("markets") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    markets = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        top = row.get("top") if isinstance(row.get("top"), dict) else {}
        markets.append(
            Market(
                source=str(row.get("source") or ""),
                market_id=str(row.get("market_id") or ""),
                title=str(row.get("title") or ""),
                url=row.get("url"),
                close_time=row.get("close_time"),
                volume=row.get("volume"),
                liquidity=row.get("liquidity"),
                top=TopOfBook(**top),
                raw=row.get("raw") if isinstance(row.get("raw"), dict) else {},
            )
        )
    return markets


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.lower())[:180]
