from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prediction_arb.telegram_bot import handle_bot_command


class TelegramBotTests(unittest.TestCase):
    def test_handle_status_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "detected_at": "2026-07-01T00:00:00+00:00",
                        "opportunity_count": 1,
                        "new_count": 1,
                        "gone_count": 0,
                        "active_keys": ["YES|a|1|b|2"],
                        "opportunities": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            text = handle_bot_command("/status", path)

        self.assertIn("Monitor status", text or "")
        self.assertIn("active: 1", text or "")

    def test_handle_unknown_command_returns_none(self) -> None:
        self.assertIsNone(handle_bot_command("/unknown", Path("missing.jsonl")))

    def test_handle_capital_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.jsonl"
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
                                "sell_source": "polymarket",
                                "sell_market_id": "2",
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

            text = handle_bot_command("/capital 100 100", path)

        self.assertIn("Capital plan", text or "")
        self.assertIn("allocated=1", text or "")

    def test_handle_portfolio_command(self) -> None:
        text = handle_bot_command("/portfolio", Path("missing.jsonl"))

        self.assertIn("Paper portfolio", text or "")

    def test_handle_paper_sync_command(self) -> None:
        text = handle_bot_command("/paper_sync missing.jsonl", Path("missing.jsonl"))

        self.assertIn("Paper sync", text or "")


if __name__ == "__main__":
    unittest.main()
