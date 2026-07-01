from __future__ import annotations

import unittest

from prediction_arb.models import Market, OrderBook, OrderLevel, TopOfBook
from prediction_arb.sources.limitless import _no_book_from_yes_book


class LimitlessTests(unittest.TestCase):
    def test_no_book_is_complement_of_yes_book(self) -> None:
        yes_book = OrderBook(
            source="limitless",
            token_id="yes-token",
            outcome="YES",
            bids=[OrderLevel(price=0.2, size=10), OrderLevel(price=0.1, size=20)],
            asks=[OrderLevel(price=0.3, size=30), OrderLevel(price=0.4, size=40)],
        )
        market = Market(
            source="limitless",
            market_id="m",
            title="Test",
            url=None,
            close_time=None,
            volume=None,
            liquidity=None,
            top=TopOfBook(),
            raw={"tokens": {"no": "no-token"}},
        )

        no_book = _no_book_from_yes_book(yes_book, market)

        self.assertEqual(no_book.token_id, "no-token")
        self.assertEqual(no_book.outcome, "NO")
        self.assertEqual([(level.price, level.size) for level in no_book.bids], [(0.7, 30), (0.6, 40)])
        self.assertEqual([(level.price, level.size) for level in no_book.asks], [(0.8, 10), (0.9, 20)])


if __name__ == "__main__":
    unittest.main()

