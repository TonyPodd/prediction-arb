from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from prediction_arb.dashboard import _read_jsonl, _safe_monitor_path


class DashboardTests(unittest.TestCase):
    def test_safe_monitor_path_rejects_absolute_paths(self) -> None:
        with self.assertRaises(ValueError):
            _safe_monitor_path("/tmp/monitor.jsonl")

    def test_safe_monitor_path_rejects_parent_paths(self) -> None:
        with self.assertRaises(ValueError):
            _safe_monitor_path("../monitor.jsonl")

    def test_safe_monitor_path_accepts_relative_data_file(self) -> None:
        self.assertEqual(str(_safe_monitor_path("data/monitor.jsonl")), "data/monitor.jsonl")

    def test_read_jsonl_skips_bad_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text('{"ok": 1}\nnot-json\n{"ok": 2}\n', encoding="utf-8")

            self.assertEqual(_read_jsonl(path), [{"ok": 1}, {"ok": 2}])


if __name__ == "__main__":
    unittest.main()
