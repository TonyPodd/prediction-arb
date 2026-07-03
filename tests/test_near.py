from __future__ import annotations

import unittest
from datetime import datetime, timezone

from prediction_arb.models import DepthCandidate, ExecutionQuote
from prediction_arb.near import select_near_opportunities


def candidate(net_edge: float | None, rejection_reason: str | None) -> DepthCandidate:
    quote = ExecutionQuote("BUY", "YES", 100, 100, 0.4, 0.4, 40, True)
    return DepthCandidate(
        outcome="YES",
        buy_source="limitless",
        buy_market_id="l",
        buy_title="Buy",
        sell_source="polymarket",
        sell_market_id="p",
        sell_title="Sell",
        top_of_book_edge=net_edge,
        depth_edge=net_edge,
        net_edge=net_edge,
        safety_buffer=0.002,
        fee_estimate=0.0,
        fee_notes=[],
        rejection_reason=rejection_reason,
        executable_size=100,
        buy_quote=quote,
        sell_quote=quote,
        match_score=1,
        match_warnings=[],
        buy_url=None,
        sell_url=None,
        detected_at=datetime.now(tz=timezone.utc),
    )


class NearTests(unittest.TestCase):
    def test_select_near_opportunities_keeps_positive_rejected_profit(self) -> None:
        rows = [
            candidate(0.004, "profit_below_threshold"),
            candidate(-0.01, "net_edge_below_threshold"),
            candidate(0.02, None),
        ]

        selected = select_near_opportunities(rows)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].net_edge, 0.004)


if __name__ == "__main__":
    unittest.main()
