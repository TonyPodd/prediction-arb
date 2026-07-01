from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prediction_arb.cli import _load_monitor_keys


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


if __name__ == "__main__":
    unittest.main()
