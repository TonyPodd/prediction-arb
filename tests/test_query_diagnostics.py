from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from prediction_arb.models import DepthCandidate, ExecutionQuote, Market, TopOfBook
from prediction_arb.query_diagnostics import build_query_diagnostic, latest_by_query


def candidate(reason: str | None = None, edge: float = 0.02) -> DepthCandidate:
    quote = ExecutionQuote("buy", "YES", 10, 10, 0.4, 0.4, 4.0, True)
    return DepthCandidate(
        outcome="YES",
        buy_source="kalshi",
        buy_market_id="k1",
        buy_title="BTC up",
        sell_source="polymarket",
        sell_market_id="p1",
        sell_title="BTC up",
        top_of_book_edge=edge,
        depth_edge=edge,
        net_edge=edge,
        safety_buffer=0.002,
        fee_estimate=0.001,
        fee_notes=[],
        rejection_reason=reason,
        executable_size=10,
        buy_quote=quote,
        sell_quote=quote,
        match_score=0.9,
        match_warnings=[],
        buy_url=None,
        sell_url=None,
        detected_at=datetime.now(tz=timezone.utc),
    )


class QueryDiagnosticsTests(unittest.TestCase):
    def test_build_query_diagnostic_summarizes_matching_and_rejections(self) -> None:
        kalshi_market = Market(
            "kalshi",
            "k1",
            "Will Bitcoin be above $100,000 by Jan 1, 2027?",
            None,
            None,
            None,
            None,
            TopOfBook(),
            {},
        )
        polymarket_market = Market(
            "polymarket",
            "p1",
            "Will the price of Bitcoin be above $50,000 on July 4?",
            None,
            None,
            None,
            None,
            TopOfBook(),
            {},
        )

        with patch("prediction_arb.query_diagnostics.scan_depth_candidates", side_effect=[[candidate()], [candidate("profit_below_min"), candidate()]]):
            payload = build_query_diagnostic(
                query="btc",
                kalshi_markets=[kalshi_market],
                polymarket_markets=[polymarket_market],
                size=10,
                min_match_score=0.1,
                min_net_edge=0.005,
                min_profit=1.0,
                safety_buffer=0.002,
                fee_bps=50,
                route_fixed_costs={"*": 2},
                route_cost_bps={"*": 25},
                max_depth_pairs=2,
            )

        self.assertEqual(payload["type"], "query_diagnostic")
        self.assertEqual(payload["source_counts"], {"kalshi": 1, "polymarket": 1})
        self.assertEqual(payload["passing_count"], 1)
        self.assertEqual(payload["rejection_counts"], {"profit_below_min": 1})
        self.assertEqual(payload["best_passing"]["route"], "kalshi->polymarket")
        self.assertEqual(len(payload["matching"]["rejected_examples"]), 1)
        self.assertIn("threshold_differs", payload["matching"]["rejected_examples"][0]["warnings"])

    def test_latest_by_query_keeps_last_row_per_query(self) -> None:
        rows = [
            {"type": "query_diagnostic", "query": "btc", "value": 1},
            {"type": "other", "query": "eth", "value": 2},
            {"type": "query_diagnostic", "query": "btc", "value": 3},
            {"type": "query_diagnostic", "query": "eth", "value": 4},
        ]

        self.assertEqual(latest_by_query(rows), [{"type": "query_diagnostic", "query": "btc", "value": 3}, {"type": "query_diagnostic", "query": "eth", "value": 4}])


if __name__ == "__main__":
    unittest.main()
