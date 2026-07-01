from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from datetime import datetime, timedelta, timezone

from argparse import Namespace

from prediction_arb.cli import _filter_by_close_window, _filter_by_any_category, _load_dotenv, _load_monitor_keys, _monitor_error_payload, _telegram_send_message_url, _validate_monitor_scope
from prediction_arb.models import Market, TopOfBook


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

    def test_filter_by_close_window_keeps_short_term_markets(self) -> None:
        near = Market("test", "near", "Near", None, (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat(), None, None, TopOfBook(), {})
        far = Market("test", "far", "Far", None, (datetime.now(tz=timezone.utc) + timedelta(days=3)).isoformat(), None, None, TopOfBook(), {})

        rows = _filter_by_close_window([near, far], min_close_minutes=1, max_close_hours=24)

        self.assertEqual([item.market_id for item in rows], ["near"])

    def test_filter_by_close_window_rejects_already_closed_markets(self) -> None:
        closed = Market("test", "closed", "Closed", None, (datetime.now(tz=timezone.utc) - timedelta(minutes=5)).isoformat(), None, None, TopOfBook(), {})
        near = Market("test", "near", "Near", None, (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat(), None, None, TopOfBook(), {})

        rows = _filter_by_close_window([closed, near], min_close_minutes=None, max_close_hours=24)

        self.assertEqual([item.market_id for item in rows], ["near"])

    def test_filter_by_any_category_uses_tags_and_categories(self) -> None:
        crypto = Market("test", "crypto", "BTC Up or Down", None, None, None, None, TopOfBook(), {"categories": ["Crypto"], "tags": ["15 min"]})
        sports = Market("test", "sports", "Team wins", None, None, None, None, TopOfBook(), {"categories": ["Sports"]})

        rows = _filter_by_any_category([crypto, sports], ["crypto"])

        self.assertEqual([item.market_id for item in rows], ["crypto"])

    def test_validate_monitor_scope_requires_some_universe(self) -> None:
        with self.assertRaises(ValueError):
            _validate_monitor_scope(Namespace(query=[], category=[], all_markets=False))

        _validate_monitor_scope(Namespace(query=["btc"], category=[], all_markets=False))


if __name__ == "__main__":
    unittest.main()
