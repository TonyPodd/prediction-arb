from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prediction_arb.reporting import summarize_monitor_history


class ReportingTests(unittest.TestCase):
    def test_summarize_monitor_history_ranks_best_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.jsonl"
            rows = [
                {
                    "detected_at": "2026-07-01T00:00:00+00:00",
                    "opportunity_count": 1,
                    "new_count": 1,
                    "gone_count": 0,
                    "active_keys": ["YES|a|1|b|2"],
                    "opportunities": [
                        {
                            "outcome": "YES",
                            "buy_source": "a",
                            "buy_market_id": "1",
                            "buy_title": "Buy",
                            "sell_source": "b",
                            "sell_market_id": "2",
                            "sell_title": "Sell",
                            "net_edge": 0.01,
                            "executable_size": 100,
                            "fee_estimate": 0.001,
                        }
                    ],
                },
                {
                    "detected_at": "2026-07-01T00:01:00+00:00",
                    "opportunity_count": 1,
                    "new_count": 0,
                    "gone_count": 0,
                    "active_keys": ["YES|a|1|b|2"],
                    "opportunities": [
                        {
                            "outcome": "YES",
                            "buy_source": "a",
                            "buy_market_id": "1",
                            "buy_title": "Buy",
                            "sell_source": "b",
                            "sell_market_id": "2",
                            "sell_title": "Sell",
                            "net_edge": 0.02,
                            "executable_size": 100,
                            "fee_estimate": 0.001,
                        }
                    ],
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            summary = summarize_monitor_history(path)

        self.assertEqual(summary["snapshots"], 2)
        self.assertEqual(summary["total_new_events"], 1)
        self.assertEqual(summary["latest_active_count"], 1)
        self.assertEqual(summary["unique_routes_seen"], 1)
        self.assertEqual(summary["best_routes"][0]["net_edge"], 0.02)
        self.assertEqual(summary["best_routes"][0]["estimated_profit"], 2.0)

    def test_summarize_monitor_history_handles_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = summarize_monitor_history(Path(tmp) / "missing.jsonl")

        self.assertEqual(summary["snapshots"], 0)
        self.assertEqual(summary["best_routes"], [])


if __name__ == "__main__":
    unittest.main()
