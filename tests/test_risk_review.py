from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from prediction_arb.models import DepthCandidate, ExecutionQuote
from prediction_arb.review_store import append_review_candidates, append_review_label, load_review_queue
from prediction_arb.risk import assess_candidate_risk


def candidate(**kwargs: object) -> DepthCandidate:
    base = {
        "outcome": "YES",
        "buy_source": "limitless",
        "buy_market_id": "l1",
        "buy_title": "BTC Up or Down",
        "sell_source": "polymarket",
        "sell_market_id": "p1",
        "sell_title": "Bitcoin Up or Down",
        "top_of_book_edge": 0.03,
        "depth_edge": 0.03,
        "net_edge": 0.03,
        "safety_buffer": 0.002,
        "fee_estimate": 0.001,
        "fee_notes": [],
        "rejection_reason": None,
        "executable_size": 100.0,
        "buy_quote": ExecutionQuote("buy", "YES", 100, 100, 0.4, 0.4, 40, True),
        "sell_quote": ExecutionQuote("sell", "YES", 100, 100, 0.43, 0.43, 43, True),
        "match_score": 0.8,
        "match_warnings": [],
        "buy_url": None,
        "sell_url": None,
        "detected_at": datetime.now(tz=timezone.utc),
    }
    base.update(kwargs)
    return DepthCandidate(**base)


class RiskReviewTests(unittest.TestCase):
    def test_extreme_edge_requires_manual_review(self) -> None:
        risk = assess_candidate_risk(candidate(net_edge=0.55))

        self.assertEqual(risk["risk_level"], "high")
        self.assertTrue(risk["manual_review"])
        self.assertIn("extreme_net_edge", risk["reasons"])

    def test_price_source_warning_requires_manual_review(self) -> None:
        risk = assess_candidate_risk(candidate(match_warnings=["price_source_differs"]))

        self.assertTrue(risk["manual_review"])
        self.assertIn("price_source_differs", risk["reasons"])

    def test_review_store_roundtrip_with_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            review_path = Path(tmp) / "review.jsonl"
            label_path = Path(tmp) / "labels.jsonl"
            records = append_review_candidates([candidate(net_edge=0.4)], review_path)
            append_review_label(str(records[0]["review_id"]), "same_event", label_path, actor="test")

            rows = load_review_queue(review_path, label_path)

        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["label"] or {})["label"], "same_event")


if __name__ == "__main__":
    unittest.main()
