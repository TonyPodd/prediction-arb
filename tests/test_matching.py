from __future__ import annotations

import unittest

from prediction_arb.matching import condition_from_market, market_match_details
from prediction_arb.models import Market, TopOfBook


def market(source: str, title: str, close_time: str = "2026-12-31T00:00:00Z") -> Market:
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


def raw_market(source: str, title: str, raw: dict, close_time: str = "2026-12-31T00:00:00Z") -> Market:
    item = market(source, title, close_time)
    return Market(item.source, item.market_id, item.title, item.url, item.close_time, item.volume, item.liquidity, item.top, raw)


class MatchingTests(unittest.TestCase):
    def test_by_end_and_before_year_share_semantic_deadline(self) -> None:
        left = market("limitless", "Will China invade Taiwan by end of 2026?", "2027-01-01T04:59:00Z")
        right = market("polymarket", "Will China invade Taiwan before 2027?", "2026-12-31T00:00:00Z")

        details = market_match_details(left, right)

        self.assertEqual(details.left_condition_kind, "deadline_yes_no")
        self.assertEqual(details.right_condition_kind, "deadline_yes_no")
        self.assertEqual(details.left_condition.deadline, "2026-end")
        self.assertEqual(details.right_condition.deadline, "2026-end")
        self.assertNotIn("deadline_differs", details.warnings)

    def test_directional_market_does_not_match_threshold_market(self) -> None:
        left = market("limitless", "BTC Up or Down - Daily", "2026-07-01T16:00:00Z")
        right = market("polymarket", "Will the price of Bitcoin be above $62,000 on July 1?", "2026-07-01T16:00:00Z")

        details = market_match_details(left, right)

        self.assertIn("condition_kind_differs", details.warnings)
        self.assertEqual(details.left_condition.asset, "btc")
        self.assertEqual(details.right_condition.asset, "btc")
        self.assertEqual(details.right_condition.threshold, 62000.0)

    def test_condition_extracts_bitcoin_reserve_deadline(self) -> None:
        parsed = condition_from_market(market("limitless", "US national Bitcoin reserve before 2027?"))

        self.assertEqual(parsed.kind, "deadline_yes_no")
        self.assertEqual(parsed.asset, "btc")
        self.assertEqual(parsed.deadline, "2026-end")

    def test_directional_interval_matches(self) -> None:
        left = market("limitless", "BTC Up or Down - 15 Min", "2026-07-01T18:15:00Z")
        right = market("polymarket", "Bitcoin Up or Down - 15 minutes", "2026-07-01T18:15:00Z")

        details = market_match_details(left, right)

        self.assertEqual(details.left_condition.kind, "directional_up_down")
        self.assertEqual(details.left_condition.asset, "btc")
        self.assertEqual(details.left_condition.interval_minutes, 15)
        self.assertEqual(details.right_condition.interval_minutes, 15)
        self.assertNotIn("interval_differs", details.warnings)

    def test_directional_interval_differs(self) -> None:
        left = market("limitless", "BTC Up or Down - 5 Min", "2026-07-01T18:15:00Z")
        right = market("polymarket", "Bitcoin Up or Down - 15 minutes", "2026-07-01T18:15:00Z")

        details = market_match_details(left, right)

        self.assertIn("interval_differs", details.warnings)

    def test_interval_extracts_compact_slug_units(self) -> None:
        parsed = condition_from_market(
            raw_market("polymarket", "Bitcoin Up or Down - July 1, 2:10PM-2:15PM ET", {"slug": "btc-updown-5m-1782929400"})
        )

        self.assertEqual(parsed.interval_minutes, 5)

    def test_dated_up_down_uses_daily_interval_not_resolution_candle(self) -> None:
        parsed = condition_from_market(
            raw_market(
                "polymarket",
                "Bitcoin Up or Down on July 2?",
                {
                    "slug": "bitcoin-up-or-down-on-july-2-2026",
                    "description": "This resolves using the Binance 1 minute candle for BTC/USDT.",
                },
            )
        )

        self.assertEqual(parsed.interval_minutes, 1440)

    def test_price_source_and_pair_warnings_are_soft(self) -> None:
        left = raw_market(
            "limitless",
            "BTC Up or Down - Hourly",
            {"description": "Resolution source is Chainlink BTC/USD."},
            "2026-07-01T19:00:00Z",
        )
        right = raw_market(
            "polymarket",
            "Bitcoin Up or Down - July 1, 2PM ET",
            {"description": "Resolution source is Binance BTC/USDT close prices."},
            "2026-07-01T19:00:00Z",
        )

        details = market_match_details(left, right)

        self.assertEqual(details.left_condition.price_source, "chainlink")
        self.assertEqual(details.right_condition.price_source, "binance")
        self.assertEqual(details.left_condition.price_pair, "btc/usd")
        self.assertEqual(details.right_condition.price_pair, "btc/usdt")
        self.assertIn("price_source_differs", details.warnings)
        self.assertIn("price_pair_differs", details.warnings)

    def test_polymarket_hourly_title_extracts_interval(self) -> None:
        parsed = condition_from_market(market("polymarket", "Bitcoin Up or Down - July 2, 6AM ET"))

        self.assertEqual(parsed.interval_minutes, 60)

    def test_polymarket_dated_up_down_extracts_daily_interval(self) -> None:
        parsed = condition_from_market(market("polymarket", "S&P 500 (SPX) Up or Down on July 2?"))

        self.assertEqual(parsed.interval_minutes, 1440)

    def test_open_up_down_differs_from_close_directional_market(self) -> None:
        left = market("limitless", "S&P 500 ETF (SPY) Up or Down - Weekly")
        right = market("polymarket", "S&P 500 (SPX) Opens Up or Down on July 2?")

        details = market_match_details(left, right)

        self.assertEqual(details.left_condition.kind, "directional_up_down")
        self.assertEqual(details.right_condition.kind, "open_up_down")
        self.assertIn("condition_kind_differs", details.warnings)


if __name__ == "__main__":
    unittest.main()
