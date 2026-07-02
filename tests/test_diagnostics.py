from __future__ import annotations

import unittest
import json
from datetime import datetime, timezone
from unittest.mock import patch

from prediction_arb.diagnostics import build_health_report
from prediction_arb.models import Market, OrderBook, OrderLevel, TopOfBook


class DiagnosticsTests(unittest.TestCase):
    def test_health_report_counts_matching_and_scan_stages(self) -> None:
        close = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
        left = Market("limitless", "l1", "BTC Up or Down - Daily", None, close, None, None, TopOfBook(), {})
        right = Market("polymarket", "p1", "Bitcoin Up or Down on July 2?", None, close, None, None, TopOfBook(), {"feesEnabled": False})
        books = {
            ("limitless", "YES"): OrderBook("limitless", "l-y", "YES", bids=[OrderLevel(0.6, 100)], asks=[OrderLevel(0.7, 100)]),
            ("polymarket", "YES"): OrderBook("polymarket", "p-y", "YES", bids=[OrderLevel(0.8, 100)], asks=[OrderLevel(0.9, 100)]),
            ("limitless", "NO"): OrderBook("limitless", "l-n", "NO", bids=[], asks=[]),
            ("polymarket", "NO"): OrderBook("polymarket", "p-n", "NO", bids=[], asks=[]),
        }

        with patch("prediction_arb.depth._fetch_book", side_effect=lambda market, outcome: books[(market.source, outcome)]):
            report = build_health_report([left], [right], size=10, min_net_edge=0.01, safety_buffer=0, fee_bps=0, min_profit=0)

        self.assertEqual(report["matching"]["pairs_checked"], 1)
        self.assertEqual(report["matching"]["structurally_compatible_pairs"], 1)
        self.assertGreaterEqual(report["scans"]["no_costs"]["passing_count"], 1)
        self.assertEqual(report["verdict"]["status"], "healthy_with_opportunities")
        json.dumps(report)


if __name__ == "__main__":
    unittest.main()
