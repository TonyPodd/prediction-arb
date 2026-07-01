from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prediction_arb.paper import paper_enter_from_monitor, paper_mark_close
from prediction_arb.portfolio import initialize_portfolio, load_portfolio, portfolio_summary


def write_monitor(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "detected_at": "2026-07-01T00:00:00+00:00",
                "opportunity_count": 1,
                "new_count": 1,
                "gone_count": 0,
                "active_keys": ["YES|limitless|1|polymarket|2"],
                "opportunities": [
                    {
                        "outcome": "YES",
                        "buy_source": "limitless",
                        "buy_market_id": "1",
                        "buy_title": "Buy",
                        "sell_source": "polymarket",
                        "sell_market_id": "2",
                        "sell_title": "Sell",
                        "net_edge": 0.02,
                        "executable_size": 100,
                        "buy_quote": {"notional": 20},
                        "sell_quote": {"filled_size": 100},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


class PaperTests(unittest.TestCase):
    def test_initialize_and_load_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.json"
            initialize_portfolio(path, {"limitless": 10, "polymarket": 20})
            portfolio = load_portfolio(path)

        self.assertEqual(portfolio["cash"], {"limitless": 10.0, "polymarket": 20.0})

    def test_paper_enter_opens_position_and_reserves_cash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            monitor = Path(tmp) / "monitor.jsonl"
            portfolio = Path(tmp) / "portfolio.json"
            write_monitor(monitor)
            initialize_portfolio(portfolio, {"limitless": 100, "polymarket": 100})

            result = paper_enter_from_monitor(monitor, portfolio)

        self.assertEqual(result["entered_count"], 1)
        self.assertEqual(result["portfolio"]["open_count"], 1)
        self.assertEqual(result["portfolio"]["cash"]["limitless"], 80.0)

    def test_paper_close_moves_position_to_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            monitor = Path(tmp) / "monitor.jsonl"
            portfolio = Path(tmp) / "portfolio.json"
            write_monitor(monitor)
            initialize_portfolio(portfolio, {"limitless": 100, "polymarket": 100})
            enter = paper_enter_from_monitor(monitor, portfolio)
            key = enter["entered"][0]["key"]

            result = paper_mark_close(portfolio, key, realized_pnl=1.5)

        self.assertEqual(result["portfolio"]["open_count"], 0)
        self.assertEqual(result["portfolio"]["closed_count"], 1)
        self.assertEqual(result["portfolio"]["realized_pnl"], 1.5)

    def test_portfolio_summary(self) -> None:
        summary = portfolio_summary({"cash": {"limitless": 1}, "inventory": {}, "open_positions": [], "closed_positions": [], "rejected": []})

        self.assertEqual(summary["open_count"], 0)


if __name__ == "__main__":
    unittest.main()
