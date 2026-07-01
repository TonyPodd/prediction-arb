from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prediction_arb.cli import _load_dotenv, _load_monitor_keys, _monitor_error_payload, _telegram_send_message_url


class CliTests(unittest.TestCase):
    def test_load_monitor_keys_reads_last_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"active_keys": ["old"]}),
                        json.dumps({"active_keys": ["new", "still"]}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(_load_monitor_keys(path), {"new", "still"})

    def test_load_monitor_keys_tolerates_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_load_monitor_keys(Path(tmp) / "missing.jsonl"), set())

    def test_monitor_error_payload_marks_error_rows(self) -> None:
        payload = _monitor_error_payload("taiwan", RuntimeError("boom"))

        self.assertEqual(payload["type"], "error")
        self.assertEqual(payload["query"], "taiwan")
        self.assertEqual(payload["error"], "RuntimeError: boom")

    def test_telegram_send_message_url(self) -> None:
        self.assertEqual(_telegram_send_message_url("TOKEN"), "https://api.telegram.org/botTOKEN/sendMessage")

    def test_load_dotenv_reads_missing_env_values(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('EXAMPLE_TOKEN="abc"\n', encoding="utf-8")
            old_value = os.environ.pop("EXAMPLE_TOKEN", None)
            try:
                _load_dotenv(path)
                self.assertEqual(os.environ.get("EXAMPLE_TOKEN"), "abc")
            finally:
                os.environ.pop("EXAMPLE_TOKEN", None)
                if old_value is not None:
                    os.environ["EXAMPLE_TOKEN"] = old_value


if __name__ == "__main__":
    unittest.main()
