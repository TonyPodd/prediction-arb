from __future__ import annotations

import unittest
from unittest.mock import patch

from prediction_arb.depth import _geometric_sizes, find_max_depth_size, quote_execution, scan_depth_candidates, sweep_depth
from prediction_arb.models import Market, OrderBook, OrderLevel, TopOfBook


def book(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderBook:
    return OrderBook(
        source="test",
        token_id="token",
        outcome="YES",
        bids=[OrderLevel(price=price, size=size) for price, size in bids],
        asks=[OrderLevel(price=price, size=size) for price, size in asks],
    )


class DepthTests(unittest.TestCase):
    def test_buy_full_fill_at_one_level(self) -> None:
        quote = quote_execution(book([], [(0.1, 100)]), "BUY", 50)

        self.assertTrue(quote.complete)
        self.assertEqual(quote.filled_size, 50)
        self.assertEqual(quote.avg_price, 0.1)
        self.assertEqual(quote.worst_price, 0.1)
        self.assertEqual(quote.notional, 5)

    def test_buy_fills_across_multiple_ask_levels(self) -> None:
        quote = quote_execution(book([], [(0.2, 10), (0.3, 10)]), "BUY", 15)

        self.assertTrue(quote.complete)
        self.assertEqual(quote.filled_size, 15)
        self.assertAlmostEqual(quote.avg_price, (0.2 * 10 + 0.3 * 5) / 15)
        self.assertEqual(quote.worst_price, 0.3)

    def test_incomplete_fill_rejected_by_default(self) -> None:
        quote = quote_execution(book([], [(0.2, 10)]), "BUY", 15)

        self.assertFalse(quote.complete)
        self.assertEqual(quote.filled_size, 10)
        self.assertIsNone(quote.avg_price)

    def test_sell_uses_highest_bids_first(self) -> None:
        quote = quote_execution(book([(0.4, 10), (0.5, 10)], []), "SELL", 15)

        self.assertTrue(quote.complete)
        self.assertAlmostEqual(quote.avg_price, (0.5 * 10 + 0.4 * 5) / 15)
        self.assertEqual(quote.worst_price, 0.4)

    def test_fee_bps_reduces_net_edge(self) -> None:
        buy_market = Market("limitless", "buy", "Buy", None, None, None, None, TopOfBook(), {})
        sell_market = Market("polymarket", "sell", "Sell", None, None, None, None, TopOfBook(), {"feesEnabled": False})
        from prediction_arb.depth import _fee_estimate_per_share

        fee, notes = _fee_estimate_per_share(buy_market, sell_market, 0.10, 0.20, 100)

        self.assertAlmostEqual(fee, 0.003)
        self.assertIn("manual_fee_bps=100", notes)

    def test_polymarket_disabled_fees_are_zero(self) -> None:
        market = Market("polymarket", "m", "M", None, None, None, None, TopOfBook(), {"feesEnabled": False})
        from prediction_arb.depth import _market_taker_fee_per_share

        fee, notes = _market_taker_fee_per_share(market, 0.5)

        self.assertEqual(fee, 0.0)
        self.assertIn("polymarket_fees_disabled", notes)

    def test_polymarket_fee_schedule_uses_price_uncertainty_formula(self) -> None:
        market = Market(
            "polymarket",
            "m",
            "M",
            None,
            None,
            None,
            None,
            TopOfBook(),
            {"feesEnabled": True, "feeSchedule": {"rate": 0.06, "exponent": 1}},
        )
        from prediction_arb.depth import _market_taker_fee_per_share

        fee, notes = _market_taker_fee_per_share(market, 0.5)

        self.assertAlmostEqual(fee, 0.015)
        self.assertIn("polymarket_fee_rate=0.06", notes)
        self.assertIn("polymarket_fee_rounded_5dp", notes)

    def test_polymarket_fee_rounds_tiny_values_to_zero(self) -> None:
        market = Market(
            "polymarket",
            "m",
            "M",
            None,
            None,
            None,
            None,
            TopOfBook(),
            {"feesEnabled": True, "feeSchedule": {"rate": 0.0001, "exponent": 1}},
        )
        from prediction_arb.depth import _market_taker_fee_per_share

        fee, notes = _market_taker_fee_per_share(market, 0.01)

        self.assertEqual(fee, 0.0)
        self.assertIn("polymarket_fee_rounded_5dp", notes)

    def test_kalshi_fee_uses_expected_earnings_and_rounds_total_up_to_cent(self) -> None:
        market = Market("kalshi", "m", "M", None, None, None, None, TopOfBook(), {})
        from prediction_arb.depth import _fee_estimate_per_share, _market_taker_fee_per_share

        fee, notes = _market_taker_fee_per_share(market, 0.5, size=10)

        self.assertEqual(fee, 0.018)
        self.assertIn("kalshi_taker_fee_rate=0.07", notes)
        self.assertIn("kalshi_fee_rounded_up_cent", notes)

        total_fee, _ = _fee_estimate_per_share(market, Market("polymarket", "p", "P", None, None, None, None, TopOfBook(), {"feesEnabled": False}), 0.5, 0.5, 0, executable_size=10)
        self.assertEqual(total_fee, 0.018)

    def test_missing_manual_fee_buffer_is_called_out(self) -> None:
        buy_market = Market("limitless", "buy", "Buy", None, None, None, None, TopOfBook(), {})
        sell_market = Market("polymarket", "sell", "Sell", None, None, None, None, TopOfBook(), {"feesEnabled": False})
        from prediction_arb.depth import _fee_estimate_per_share

        _, notes = _fee_estimate_per_share(buy_market, sell_market, 0.10, 0.20, 0)

        self.assertIn("limitless_no_fee_field", notes)
        self.assertIn("manual_fee_buffer_missing", notes)

    def test_route_fixed_cost_is_divided_by_size(self) -> None:
        buy_market = Market("polymarket", "buy", "Buy", None, None, None, None, TopOfBook(), {"feesEnabled": False})
        sell_market = Market("limitless", "sell", "Sell", None, None, None, None, TopOfBook(), {})
        from prediction_arb.depth import _fee_estimate_per_share

        fee, notes = _fee_estimate_per_share(
            buy_market,
            sell_market,
            0.40,
            0.45,
            0,
            executable_size=100,
            route_fixed_costs={"polymarket->limitless": 2.0},
        )

        self.assertAlmostEqual(fee, 0.02)
        self.assertIn("route_fixed_cost_usdc=polymarket->limitless:2.0", notes)

    def test_route_cost_bps_applies_to_buy_and_sell_prices(self) -> None:
        buy_market = Market("polymarket", "buy", "Buy", None, None, None, None, TopOfBook(), {"feesEnabled": False})
        sell_market = Market("limitless", "sell", "Sell", None, None, None, None, TopOfBook(), {})
        from prediction_arb.depth import _fee_estimate_per_share

        fee, notes = _fee_estimate_per_share(
            buy_market,
            sell_market,
            0.40,
            0.45,
            0,
            executable_size=100,
            route_cost_bps={"*": 25},
        )

        self.assertAlmostEqual(fee, (0.40 + 0.45) * 0.0025)
        self.assertIn("route_cost_bps=polymarket->limitless:25.0", notes)

    def test_public_fee_estimator_matches_market_fee(self) -> None:
        market = Market("polymarket", "m", "M", None, None, None, None, TopOfBook(), {"feesEnabled": False})
        from prediction_arb.depth import estimate_market_taker_fee_per_share

        fee, notes = estimate_market_taker_fee_per_share(market, 0.5)

        self.assertEqual(fee, 0.0)
        self.assertIn("polymarket_fees_disabled", notes)


class DepthCandidateTests(unittest.TestCase):
    def test_filtered_candidates_can_be_returned(self) -> None:
        left = Market(
            source="limitless",
            market_id="left",
            title="Will China invade Taiwan by end of 2026?",
            url=None,
            close_time="2027-01-01T04:59:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.1, yes_ask=0.2),
            raw={},
        )
        right = Market(
            source="polymarket",
            market_id="right",
            title="Will China invade Taiwan before 2027?",
            url=None,
            close_time="2026-12-31T00:00:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.1, yes_ask=0.2),
            raw={"clobTokenIds": "[]"},
        )

        empty_book = OrderBook(source="test", token_id="t", outcome="YES", bids=[], asks=[])
        with patch("prediction_arb.depth._fetch_book", return_value=empty_book):
            rows = scan_depth_candidates([left], [right], size=10, include_filtered=True, min_match_score=0.01)

        self.assertTrue(rows)
        self.assertEqual(rows[0].rejection_reason, "incomplete_fill")

    def test_sweep_reports_best_profit(self) -> None:
        left = Market(
            source="limitless",
            market_id="left",
            title="Will China invade Taiwan by end of 2026?",
            url=None,
            close_time="2027-01-01T04:59:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.2, yes_ask=0.3),
            raw={},
        )
        right = Market(
            source="polymarket",
            market_id="right",
            title="Will China invade Taiwan before 2027?",
            url=None,
            close_time="2026-12-31T00:00:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.4, yes_ask=0.5),
            raw={"feesEnabled": False},
        )
        books = {
            ("limitless", "YES"): OrderBook("limitless", "l", "YES", bids=[OrderLevel(0.4, 100)], asks=[OrderLevel(0.5, 100)]),
            ("polymarket", "YES"): OrderBook("polymarket", "p", "YES", bids=[OrderLevel(0.2, 100)], asks=[OrderLevel(0.3, 100)]),
            ("limitless", "NO"): OrderBook("limitless", "l-no", "NO", bids=[], asks=[]),
            ("polymarket", "NO"): OrderBook("polymarket", "p-no", "NO", bids=[], asks=[]),
        }

        with patch("prediction_arb.depth._fetch_book", side_effect=lambda market, outcome: books[(market.source, outcome)]):
            rows = sweep_depth([left], [right], sizes=[10], min_net_edge=0.01, safety_buffer=0)

        self.assertAlmostEqual(rows[0].best_net_edge, 0.1)
        self.assertAlmostEqual(rows[0].best_net_profit, 1.0)
        self.assertEqual(rows[0].best_route, "polymarket->limitless")

    def test_min_profit_filters_small_profitable_edges(self) -> None:
        left = Market(
            source="limitless",
            market_id="left",
            title="Will China invade Taiwan by end of 2026?",
            url=None,
            close_time="2027-01-01T04:59:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.2, yes_ask=0.3),
            raw={},
        )
        right = Market(
            source="polymarket",
            market_id="right",
            title="Will China invade Taiwan before 2027?",
            url=None,
            close_time="2026-12-31T00:00:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.4, yes_ask=0.5),
            raw={"feesEnabled": False},
        )
        books = {
            ("limitless", "YES"): OrderBook("limitless", "l", "YES", bids=[OrderLevel(0.4, 100)], asks=[OrderLevel(0.5, 100)]),
            ("polymarket", "YES"): OrderBook("polymarket", "p", "YES", bids=[OrderLevel(0.2, 100)], asks=[OrderLevel(0.3, 100)]),
            ("limitless", "NO"): OrderBook("limitless", "l-no", "NO", bids=[], asks=[]),
            ("polymarket", "NO"): OrderBook("polymarket", "p-no", "NO", bids=[], asks=[]),
        }

        with patch("prediction_arb.depth._fetch_book", side_effect=lambda market, outcome: books[(market.source, outcome)]):
            rows = scan_depth_candidates([left], [right], size=10, min_net_edge=0.01, safety_buffer=0, min_profit=2.0, include_filtered=True)

        self.assertTrue(rows)
        self.assertEqual(rows[0].rejection_reason, "profit_below_threshold")

    def test_outcome_subject_mismatch_filters_sports_false_edge(self) -> None:
        kalshi_cape_verde = Market(
            source="kalshi",
            market_id="KXWCADVANCE-26JUL03ARGCPV-CPV",
            title="Argentina vs Cape Verde: To Advance",
            url=None,
            close_time="2026-07-04T01:00:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.07, yes_ask=0.08),
            raw={"yes_sub_title": "Cape Verde advances"},
        )
        polymarket = Market(
            source="polymarket",
            market_id="2721765",
            title="Argentina vs. Cabo Verde: Team to Advance",
            url=None,
            close_time="2026-07-03T22:00:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.93, yes_ask=0.94),
            raw={"outcomes": '["Argentina", "Cabo Verde"]', "feesEnabled": False},
        )
        books = {
            ("kalshi", "YES"): OrderBook("kalshi", "k", "YES", bids=[OrderLevel(0.07, 100)], asks=[OrderLevel(0.08, 100)]),
            ("polymarket", "YES"): OrderBook("polymarket", "p", "YES", bids=[OrderLevel(0.93, 100)], asks=[OrderLevel(0.94, 100)]),
            ("kalshi", "NO"): OrderBook("kalshi", "k-no", "NO", bids=[], asks=[]),
            ("polymarket", "NO"): OrderBook("polymarket", "p-no", "NO", bids=[], asks=[]),
        }

        with patch("prediction_arb.depth._fetch_book", side_effect=lambda market, outcome: books[(market.source, outcome)]):
            rows = scan_depth_candidates(
                [kalshi_cape_verde],
                [polymarket],
                size=10,
                min_net_edge=0.01,
                safety_buffer=0,
                include_filtered=True,
            )

        self.assertTrue(rows)
        self.assertEqual(rows[0].rejection_reason, "outcome_subject_differs")
        self.assertIn("outcome_subject_differs", rows[0].match_warnings)

    def test_geometric_sizes_include_max(self) -> None:
        self.assertEqual(_geometric_sizes(10, 100, 2), [10.0, 20.0, 40.0, 80.0, 100.0])

    def test_find_max_depth_size_returns_last_passing_size(self) -> None:
        left = Market(
            source="limitless",
            market_id="left",
            title="Will China invade Taiwan by end of 2026?",
            url=None,
            close_time="2027-01-01T04:59:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.2, yes_ask=0.3),
            raw={},
        )
        right = Market(
            source="polymarket",
            market_id="right",
            title="Will China invade Taiwan before 2027?",
            url=None,
            close_time="2026-12-31T00:00:00Z",
            volume=None,
            liquidity=None,
            top=TopOfBook(yes_bid=0.4, yes_ask=0.5),
            raw={"feesEnabled": False},
        )
        books = {
            ("limitless", "YES"): OrderBook("limitless", "l", "YES", bids=[OrderLevel(0.4, 50)], asks=[OrderLevel(0.5, 50)]),
            ("polymarket", "YES"): OrderBook("polymarket", "p", "YES", bids=[OrderLevel(0.2, 50)], asks=[OrderLevel(0.3, 50)]),
            ("limitless", "NO"): OrderBook("limitless", "l-no", "NO", bids=[], asks=[]),
            ("polymarket", "NO"): OrderBook("polymarket", "p-no", "NO", bids=[], asks=[]),
        }

        with patch("prediction_arb.depth._fetch_book", side_effect=lambda market, outcome: books[(market.source, outcome)]):
            result = find_max_depth_size("taiwan", [left], [right], min_size=10, max_size=100, step_multiplier=2, min_net_edge=0.01, safety_buffer=0)

        self.assertEqual(result.max_passing_size, 40.0)
        self.assertIsNotNone(result.best_at_max_size)


if __name__ == "__main__":
    unittest.main()
