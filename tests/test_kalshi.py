from __future__ import annotations

import unittest
from unittest.mock import patch

from prediction_arb.models import Market, TopOfBook
from prediction_arb.sources import kalshi


class KalshiTests(unittest.TestCase):
    def test_fetch_markets_uses_open_non_mve_markets(self) -> None:
        payload = {
            "markets": [
                {"ticker": "KXMVE-1", "title": "Combo", "market_type": "binary", "status": "active"},
                {
                    "ticker": "KXBTC-1",
                    "title": "Bitcoin Up or Down",
                    "market_type": "binary",
                    "status": "active",
                    "yes_bid_dollars": "0.4200",
                    "yes_ask_dollars": "0.4300",
                    "no_bid_dollars": "0.5700",
                    "no_ask_dollars": "0.5800",
                    "expected_expiration_time": "2026-07-03T16:00:00Z",
                },
            ],
            "cursor": "",
        }

        with patch("prediction_arb.sources.kalshi.get_json", return_value=payload) as get_json:
            rows = kalshi.fetch_markets(limit=10)

        self.assertEqual([row.market_id for row in rows], ["KXBTC-1"])
        self.assertEqual(rows[0].source, "kalshi")
        self.assertEqual(rows[0].top.yes_bid, 0.42)
        self.assertEqual(get_json.call_args.args[1]["mve_filter"], "exclude")

    def test_fetch_orderbook_derives_yes_and_no_asks_from_opposite_bids(self) -> None:
        market = Market("kalshi", "KXBTC-1", "Bitcoin", None, None, None, None, TopOfBook(), {})
        payload = {
            "orderbook_fp": {
                "yes_dollars": [["0.4100", "12.00"]],
                "no_dollars": [["0.5600", "34.00"]],
            }
        }

        with patch("prediction_arb.sources.kalshi.get_json", return_value=payload):
            yes_book = kalshi.fetch_orderbook(market, "YES")
            no_book = kalshi.fetch_orderbook(market, "NO")

        self.assertEqual(yes_book.bids[0].price, 0.41)
        self.assertAlmostEqual(yes_book.asks[0].price, 0.44)
        self.assertEqual(no_book.bids[0].price, 0.56)
        self.assertAlmostEqual(no_book.asks[0].price, 0.59)

    def test_search_markets_uses_crypto_series_tags(self) -> None:
        def fake_get_json(url: str, params: dict) -> dict:
            if url.endswith("/series"):
                self.assertEqual(params["category"], "Crypto")
                self.assertEqual(params["tags"], "BTC")
                return {"series": [{"ticker": "KXBTC"}]}
            if url.endswith("/markets"):
                if "series_ticker" not in params:
                    return {"markets": [], "cursor": ""}
                self.assertEqual(params["series_ticker"], "KXBTC")
                return {
                    "markets": [
                        {
                            "ticker": "KXBTC-1",
                            "title": "Bitcoin price range",
                            "market_type": "binary",
                            "status": "active",
                            "yes_bid_dollars": "0.4200",
                            "yes_ask_dollars": "0.4300",
                        }
                    ],
                    "cursor": "",
                }
            raise AssertionError(url)

        with patch("prediction_arb.sources.kalshi.get_json", side_effect=fake_get_json):
            rows = kalshi.search_markets("bitcoin", limit=5)

        self.assertEqual([row.market_id for row in rows], ["KXBTC-1"])


if __name__ == "__main__":
    unittest.main()
