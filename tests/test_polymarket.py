from __future__ import annotations

import unittest
from unittest.mock import patch

from prediction_arb.models import Market, TopOfBook
from prediction_arb.sources.polymarket import fetch_markets, token_id_for_outcome


def market(clob_token_ids: object) -> Market:
    return Market(
        source="polymarket",
        market_id="m",
        title="Test",
        url=None,
        close_time=None,
        volume=None,
        liquidity=None,
        top=TopOfBook(),
        raw={"clobTokenIds": clob_token_ids},
    )


class PolymarketTests(unittest.TestCase):
    def test_token_mapping_yes_first_no_second(self) -> None:
        item = market('["yes-token", "no-token"]')

        self.assertEqual(token_id_for_outcome(item, "YES"), "yes-token")
        self.assertEqual(token_id_for_outcome(item, "NO"), "no-token")

    def test_missing_token_ids_return_none(self) -> None:
        item = market("[]")

        self.assertIsNone(token_id_for_outcome(item, "YES"))
        self.assertIsNone(token_id_for_outcome(item, "NO"))

    def test_malformed_token_ids_return_none(self) -> None:
        item = market("not-json")

        self.assertIsNone(token_id_for_outcome(item, "YES"))
        self.assertIsNone(token_id_for_outcome(item, "NO"))

    def test_fetch_markets_paginates_gamma_offsets(self) -> None:
        calls = []

        def fake_get_json(_url: str, params: dict) -> list[dict]:
            calls.append(dict(params))
            offset = int(params["offset"])
            limit = int(params["limit"])
            return [
                {
                    "id": str(index),
                    "question": f"Market {index}",
                    "outcomes": '["Yes","No"]',
                    "outcomePrices": '["0.4","0.6"]',
                }
                for index in range(offset, offset + limit)
            ]

        with patch("prediction_arb.sources.polymarket.get_json", side_effect=fake_get_json):
            rows = fetch_markets(limit=250)

        self.assertEqual(len(rows), 250)
        self.assertEqual([call["offset"] for call in calls], [0, 100, 200])
        self.assertEqual([call["limit"] for call in calls], [100, 100, 50])


if __name__ == "__main__":
    unittest.main()
