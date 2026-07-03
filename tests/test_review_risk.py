from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from prediction_arb.review_store import append_review_candidates, load_review_candidates
from prediction_arb.risk import assess_candidate_risk, candidate_review_id


def candidate(**overrides: object) -> SimpleNamespace:
    defaults = {
        "outcome": "YES",
        "buy_source": "kalshi",
        "buy_market_id": "KXWCGAME-ARG",
        "sell_source": "polymarket",
        "sell_market_id": "2721765",
        "net_edge": 0.066,
        "depth_edge": 0.07,
        "top_of_book_edge": 0.07,
        "match_score": 0.28,
        "match_warnings": ["competition_terms_only_on_one_side"],
        "fee_estimate": 0.00195,
        "fee_notes": ["kalshi_fee_model_not_implemented_use_manual_fee_bps", "manual_fee_buffer_missing"],
        "rejection_reason": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class ReviewRiskTests(unittest.TestCase):
    def test_sports_competition_warning_requires_manual_review(self) -> None:
        risk = assess_candidate_risk(candidate())

        self.assertTrue(risk["manual_review"])
        self.assertGreaterEqual(risk["risk_score"], 25)
        self.assertIn("sports_competition_terms_uncertain", risk["reasons"])
        self.assertIn("fee_model_uncertain", risk["reasons"])
        self.assertIn("kalshi_fee_model_uncertain", risk["reasons"])

    def test_review_id_is_stable_across_detection_times(self) -> None:
        left = candidate(detected_at="2026-07-03T15:00:00Z")
        right = candidate(detected_at="2026-07-03T15:05:00Z")

        self.assertEqual(candidate_review_id(left), candidate_review_id(right))

    def test_append_review_candidates_deduplicates_existing_review_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.jsonl"

            first = append_review_candidates([candidate(detected_at="2026-07-03T15:00:00Z")], path)
            second = append_review_candidates([candidate(detected_at="2026-07-03T15:05:00Z")], path)

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(len(load_review_candidates(path)), 1)


if __name__ == "__main__":
    unittest.main()
