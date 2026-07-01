from __future__ import annotations

import unittest

from prediction_arb.dashboard import _safe_monitor_path


class DashboardTests(unittest.TestCase):
    def test_safe_monitor_path_rejects_absolute_paths(self) -> None:
        with self.assertRaises(ValueError):
            _safe_monitor_path("/tmp/monitor.jsonl")

    def test_safe_monitor_path_rejects_parent_paths(self) -> None:
        with self.assertRaises(ValueError):
            _safe_monitor_path("../monitor.jsonl")

    def test_safe_monitor_path_accepts_relative_data_file(self) -> None:
        self.assertEqual(str(_safe_monitor_path("data/monitor.jsonl")), "data/monitor.jsonl")


if __name__ == "__main__":
    unittest.main()
