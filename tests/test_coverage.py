from __future__ import annotations

import unittest
from datetime import datetime, timezone

from prediction_arb.coverage import summarize_source_coverage
from prediction_arb.models import Market, TopOfBook


def market(source: str, title: str, close_time: str) -> Market:
    return Market(
        source=source,
        market_id=title.lower().replace(" ", "-"),
        title=title,
        url=None,
        close_time=close_time,
        volume=None,
        liquidity=None,
        top=TopOfBook(),
        raw={},
    )


class CoverageTests(unittest.TestCase):
    def test_summarizes_conditions_intervals_and_close_windows(self) -> None:
        now = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
        limitless_markets = [
            market("limitless", "BTC Up or Down - 15 Min", "2026-07-01T12:30:00Z"),
            market("limitless", "Will China invade Taiwan before 2027?", "2026-12-31T00:00:00Z"),
        ]
        polymarket_markets = [
            market("polymarket", "Bitcoin Up or Down - 15 minutes", "2026-07-01T20:00:00Z"),
        ]

        summary = summarize_source_coverage(limitless_markets, polymarket_markets, now=now)

        limitless = summary["sources"]["limitless"]
        polymarket = summary["sources"]["polymarket"]
        self.assertEqual(limitless["count"], 2)
        self.assertEqual(limitless["by_condition_kind"]["directional_up_down"], 1)
        self.assertEqual(limitless["by_asset"]["btc"], 1)
        self.assertEqual(limitless["by_interval_minutes"]["15"], 1)
        self.assertEqual(limitless["by_close_window"]["0_1h"], 1)
        self.assertEqual(polymarket["short_term_24h_count"], 1)


if __name__ == "__main__":
    unittest.main()
