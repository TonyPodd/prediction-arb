from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from prediction_arb.models import Market, TopOfBook
from prediction_arb.telegram_bot import command_reply_markup, handle_bot_command


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

        self.assertIn("Статус монитора", text or "")
        self.assertIn("Активно сейчас: 1", text or "")

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
                        "active_keys": ["YES|kalshi|1|polymarket|2"],
                        "opportunities": [
                            {
                                "outcome": "YES",
                                "buy_source": "kalshi",
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

        self.assertIn("План капитала", text or "")
        self.assertIn("выбрано=1", text or "")

    def test_handle_portfolio_command(self) -> None:
        text = handle_bot_command("/portfolio", Path("missing.jsonl"))

        self.assertIn("Бумажный портфель", text or "")

    def test_handle_paper_sync_command(self) -> None:
        text = handle_bot_command("/paper_sync missing.jsonl", Path("missing.jsonl"))

        self.assertIn("Обновление бумажного портфеля", text or "")

    def test_handle_coverage_command(self) -> None:
        close_time = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()
        kalshi_market = Market("kalshi", "k1", "Bitcoin Up or Down - 15 Min", None, close_time, None, None, TopOfBook(), {})
        polymarket_market = Market("polymarket", "p1", "Bitcoin Up or Down - 15 minutes", None, close_time, None, None, TopOfBook(), {})

        with patch("prediction_arb.telegram_bot.kalshi.fetch_markets", return_value=[kalshi_market]), patch(
            "prediction_arb.telegram_bot.polymarket.fetch_markets_expanded", return_value=[polymarket_market]
        ):
            text = handle_bot_command("/coverage 10 24", Path("missing.jsonl"))

        self.assertIn("Покрытие источников", text or "")
        self.assertIn("kalshi: рынков=1", text or "")
        self.assertIn("polymarket: рынков=1", text or "")

    def test_start_command_has_buttons(self) -> None:
        markup = command_reply_markup("/start")

        self.assertIn("keyboard", markup)
        self.assertIn("Проверка", str(markup))

    def test_russian_button_text_maps_to_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "detected_at": "2026-07-01T00:00:00+00:00",
                        "opportunity_count": 0,
                        "new_count": 0,
                        "gone_count": 0,
                        "active_keys": [],
                        "opportunities": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            text = handle_bot_command("Статус", path)

        self.assertIn("Статус монитора", text or "")

    def test_report_uses_russian_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "detected_at": "2026-07-01T00:00:00+00:00",
                        "opportunity_count": 1,
                        "new_count": 1,
                        "gone_count": 0,
                        "active_keys": ["YES|kalshi|1|polymarket|2"],
                        "opportunities": [
                            {
                                "outcome": "YES",
                                "buy_source": "kalshi",
                                "buy_market_id": "1",
                                "sell_source": "polymarket",
                                "sell_market_id": "2",
                                "net_edge": 0.02,
                                "executable_size": 100,
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            text = handle_bot_command("Отчет", path)

        self.assertIn("доходность=", text or "")
        self.assertIn("прибыль=", text or "")


if __name__ == "__main__":
    unittest.main()
