from __future__ import annotations

import unittest

from prediction_arb.models import Market, TopOfBook
from prediction_arb.sources.polymarket import token_id_for_outcome


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


if __name__ == "__main__":
    unittest.main()

