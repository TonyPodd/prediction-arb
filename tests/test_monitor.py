from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from prediction_arb.models import DepthCandidate, ExecutionQuote, Market, MonitorSnapshot, TopOfBook
from prediction_arb.monitor import _opportunity_key, build_webhook_payload, format_new_opportunity_alert, monitor_once


def candidate(outcome: str, buy_id: str, sell_id: str) -> DepthCandidate:
    quote = ExecutionQuote("BUY", outcome, 10, 10, 0.1, 0.1, 1, True)
    return DepthCandidate(
        outcome=outcome,
        buy_source="polymarket",
        buy_market_id=buy_id,
        buy_title="Buy",
        sell_source="limitless",
        sell_market_id=sell_id,
        sell_title="Sell",
        top_of_book_edge=0.02,
        depth_edge=0.02,
        net_edge=0.01,
        safety_buffer=0.002,
        fee_estimate=0.0,
        fee_notes=[],
        rejection_reason=None,
        executable_size=10,
        buy_quote=quote,
        sell_quote=quote,
        match_score=1.0,
        match_warnings=[],
        buy_url=None,
        sell_url=None,
        detected_at=datetime.now(tz=timezone.utc),
    )


class MonitorTests(unittest.TestCase):
    def test_opportunity_key_is_stable_route_identity(self) -> None:
        item = candidate("YES", "p1", "l1")

        self.assertEqual(_opportunity_key(item), "YES|polymarket|p1|limitless|l1")

    def test_monitor_once_reports_new_and_gone_keys(self) -> None:
        active = candidate("YES", "p2", "l2")
        previous = {"YES|polymarket|p1|limitless|l1"}
        market = Market("test", "m", "M", None, None, None, None, TopOfBook(), {})

        with patch("prediction_arb.monitor.scan_depth_candidates", return_value=[active]):
            snapshot, active_keys = monitor_once("taiwan", [market], [market], previous, size=100)

        self.assertEqual(snapshot.new_keys, ["YES|polymarket|p2|limitless|l2"])
        self.assertEqual(snapshot.gone_keys, ["YES|polymarket|p1|limitless|l1"])
        self.assertEqual(active_keys, {"YES|polymarket|p2|limitless|l2"})

    def test_format_new_opportunity_alert_summarizes_new_routes(self) -> None:
        item = candidate("YES", "p1", "l1")
        key = _opportunity_key(item)
        snapshot = MonitorSnapshot(
            query="taiwan",
            size=100,
            detected_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            opportunity_count=1,
            new_count=1,
            gone_count=0,
            active_keys=[key],
            new_keys=[key],
            gone_keys=[],
            opportunities=[item],
        )

        text = format_new_opportunity_alert(snapshot)

        self.assertIsNotNone(text)
        self.assertIn("New prediction-arb opportunities", text or "")
        self.assertIn("YES polymarket->limitless", text or "")
        self.assertIn("net_edge=0.0100", text or "")

    def test_format_new_opportunity_alert_returns_none_without_new_keys(self) -> None:
        snapshot = MonitorSnapshot(
            query="taiwan",
            size=100,
            detected_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            opportunity_count=0,
            new_count=0,
            gone_count=0,
            active_keys=[],
            new_keys=[],
            gone_keys=[],
            opportunities=[],
        )

        self.assertIsNone(format_new_opportunity_alert(snapshot))

    def test_webhook_payload_formats(self) -> None:
        self.assertEqual(build_webhook_payload("hello", "generic"), {"text": "hello"})
        self.assertEqual(build_webhook_payload("hello", "discord"), {"content": "hello"})


if __name__ == "__main__":
    unittest.main()
