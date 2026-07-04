from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prediction_arb.cache import cached_markets
from prediction_arb.models import Market, TopOfBook


class CacheTests(unittest.TestCase):
    def test_cached_markets_reuses_fresh_cache(self) -> None:
        calls = 0

        def fetcher() -> list[Market]:
            nonlocal calls
            calls += 1
            return [Market("kalshi", "1", "BTC", None, None, 1.0, 2.0, TopOfBook(yes_bid=0.4), {"x": 1})]

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            first = cached_markets("kalshi:btc", 60, fetcher, cache_dir=cache_dir)
            second = cached_markets("kalshi:btc", 60, fetcher, cache_dir=cache_dir)

        self.assertEqual(calls, 1)
        self.assertEqual(first, second)
        self.assertEqual(second[0].top.yes_bid, 0.4)

    def test_cached_markets_ignores_cache_when_ttl_zero(self) -> None:
        calls = 0

        def fetcher() -> list[Market]:
            nonlocal calls
            calls += 1
            return [Market("kalshi", str(calls), "BTC", None, None, None, None, TopOfBook(), {})]

        with tempfile.TemporaryDirectory() as tmp:
            cached_markets("kalshi:btc", 0, fetcher, cache_dir=Path(tmp))
            row = cached_markets("kalshi:btc", 0, fetcher, cache_dir=Path(tmp))[0]

        self.assertEqual(calls, 2)
        self.assertEqual(row.market_id, "2")


if __name__ == "__main__":
    unittest.main()
