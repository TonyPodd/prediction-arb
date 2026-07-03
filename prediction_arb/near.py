from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from prediction_arb.models import DepthCandidate


DEFAULT_NEAR_FILE = Path("data/near-opportunities.jsonl")


def select_near_opportunities(rows: list[DepthCandidate], *, min_edge: float = 0.0, top: int = 20) -> list[DepthCandidate]:
    near = [
        row
        for row in rows
        if row.rejection_reason
        and row.net_edge is not None
        and row.net_edge > min_edge
        and row.rejection_reason in {"profit_below_threshold", "net_edge_below_threshold"}
    ]
    near.sort(key=lambda row: ((row.net_edge or -999.0) * row.executable_size, row.net_edge or -999.0), reverse=True)
    return near[:top]


def append_near_opportunities(rows: list[DepthCandidate], path: Path = DEFAULT_NEAR_FILE, *, source: str = "scan") -> list[dict[str, object]]:
    records = [
        {
            "type": "near_opportunity",
            "source": source,
            "saved_at": datetime.now(tz=timezone.utc).isoformat(),
            "candidate": _serializable(asdict(row)),
            "estimated_profit": (row.net_edge or 0.0) * row.executable_size if row.net_edge is not None else None,
        }
        for row in rows
    ]
    if not records:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
    return records


def load_near_opportunities(path: Path = DEFAULT_NEAR_FILE, *, limit: int = 100) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows[-limit:] if limit > 0 else rows


def _serializable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serializable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
