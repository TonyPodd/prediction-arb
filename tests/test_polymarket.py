from __future__ import annotations

import unittest
from unittest.mock import patch

from prediction_arb.models import Market, TopOfBook
from prediction_arb.sources.polymarket import fetch_markets, fetch_markets_expanded, fetch_public_search_markets, search_markets, token_id_for_outcome


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

    def test_fetch_markets_expanded_reads_event_markets_and_skips_closed(self) -> None:
        def fake_get_json(url: str, params: dict) -> list[dict]:
            if url.endswith("/markets"):
                return []
            if url.endswith("/events"):
                return [
                    {
                        "title": "Popular event",
                        "slug": "popular-event",
                        "markets": [
                            {
                                "id": "closed",
                                "question": "Closed market?",
                                "closed": True,
                                "outcomes": '["Yes","No"]',
                                "outcomePrices": '["0.4","0.6"]',
                            },
                            {
                                "id": "open",
                                "question": "Open market?",
                                "active": True,
                                "closed": False,
                                "enableOrderBook": True,
                                "clobTokenIds": '["yes","no"]',
                                "outcomes": '["Yes","No"]',
                                "outcomePrices": '["0.4","0.6"]',
                            },
                        ],
                    }
                ]
            return []

        with patch("prediction_arb.sources.polymarket.get_json", side_effect=fake_get_json):
            rows = fetch_markets_expanded(limit=5)

        self.assertEqual([row.market_id for row in rows], ["open"])
        self.assertEqual(rows[0].url, "https://polymarket.com/event/popular-event")

    def test_fetch_public_search_markets_reads_event_markets(self) -> None:
        payload = {
            "events": [
                {
                    "title": "Bitcoin above ___ on July 3?",
                    "slug": "bitcoin-above-on-july-3-2026",
                    "markets": [
                        {
                            "id": "btc-70k",
                            "question": "Will the price of Bitcoin be above $70,000 on July 3?",
                            "active": True,
                            "closed": False,
                            "enableOrderBook": True,
                            "clobTokenIds": '["yes","no"]',
                            "outcomes": '["Yes","No"]',
                            "outcomePrices": '["0.4","0.6"]',
                        }
                    ],
                }
            ]
        }

        with patch("prediction_arb.sources.polymarket.get_json", return_value=payload):
            rows = fetch_public_search_markets("bitcoin", limit=10)

        self.assertEqual([row.market_id for row in rows], ["btc-70k"])
        self.assertEqual(rows[0].raw["eventTitle"], "Bitcoin above ___ on July 3?")
        self.assertEqual(rows[0].url, "https://polymarket.com/event/bitcoin-above-on-july-3-2026")

    def test_search_markets_merges_public_search_when_local_feed_has_no_match(self) -> None:
        public_row = Market(
            source="polymarket",
            market_id="public",
            title="Bitcoin public result",
            url=None,
            close_time=None,
            volume=None,
            liquidity=None,
            top=TopOfBook(),
            raw={},
        )

        with patch("prediction_arb.sources.polymarket.fetch_markets_expanded", return_value=[]), patch(
            "prediction_arb.sources.polymarket.fetch_public_search_markets", return_value=[public_row]
        ):
            rows = search_markets("bitcoin", limit=10)

        self.assertEqual([row.market_id for row in rows], ["public"])


if __name__ == "__main__":
    unittest.main()
