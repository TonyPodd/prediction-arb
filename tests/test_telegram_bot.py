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


if __name__ == "__main__":
    unittest.main()
