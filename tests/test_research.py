from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from prediction_arb.models import DepthCandidate, ExecutionQuote, Market, TopOfBook
from prediction_arb.research import build_research_snapshot, matching_summary


def market(source: str, market_id: str, title: str) -> Market:
    return Market(source, market_id, title, None, None, None, None, TopOfBook(), {})


def candidate(net_edge: float | None, rejection_reason: str | None) -> DepthCandidate:
    quote = ExecutionQuote("BUY", "YES", 100, 100, 0.4, 0.4, 40, True)
    return DepthCandidate(
        outcome="YES",
        buy_source="limitless",
        buy_market_id="l",
        buy_title="BTC Up or Down",
        sell_source="polymarket",
        sell_market_id="p",
        sell_title="Bitcoin Up or Down",
        top_of_book_edge=net_edge,
        depth_edge=net_edge,
        net_edge=net_edge,
        safety_buffer=0.002,
        fee_estimate=0.001,
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


class ResearchTests(unittest.TestCase):
    def test_matching_summary_counts_structural_pairs(self) -> None:
        left = [market("limitless", "l", "BTC Up or Down - 15 Min")]
        right = [market("polymarket", "p", "Bitcoin Up or Down - 15 minutes")]

        payload = matching_summary(left, right, min_match_score=0.1)

        self.assertEqual(payload["pairs_checked"], 1)
        self.assertEqual(payload["text_candidates"], 1)
        self.assertEqual(payload["structurally_compatible_pairs"], 1)

    def test_build_research_snapshot_saves_near_opportunities(self) -> None:
        rows = [candidate(0.004, "profit_below_threshold"), candidate(0.02, None)]
        with tempfile.TemporaryDirectory() as tmp:
            near_path = Path(tmp) / "near.jsonl"
            with patch("prediction_arb.research._scan_depth_pairs", return_value=rows):
                payload = build_research_snapshot(
                    scope="test",
                    limitless_markets=[],
                    polymarket_markets=[],
                    size=100,
                    min_net_edge=0.005,
                    min_profit=1,
                    safety_buffer=0.002,
                    fee_bps=50,
                    max_depth_pairs=10,
                    near_output=near_path,
                )

            self.assertEqual(payload["type"], "research_snapshot")
            self.assertEqual(payload["candidate_count"], 2)
            self.assertEqual(payload["passing_count"], 1)
            self.assertEqual(payload["near_count"], 1)
            self.assertEqual(payload["near_saved_count"], 1)
            self.assertEqual(payload["best_near"]["estimated_profit"], 0.4)
            self.assertTrue(near_path.exists())


if __name__ == "__main__":
    unittest.main()
