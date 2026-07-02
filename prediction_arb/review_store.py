from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prediction_arb.risk import assess_candidate_risk, candidate_to_dict


DEFAULT_REVIEW_FILE = Path("data/review-candidates.jsonl")
DEFAULT_LABEL_FILE = Path("data/review-labels.jsonl")


def build_review_record(candidate: object, *, source: str = "monitor") -> dict[str, object]:
    risk = assess_candidate_risk(candidate)
    return {
        "type": "review_candidate",
        "review_id": risk["review_id"],
        "status": "pending",
        "source": source,
        "detected_at": datetime.now(tz=timezone.utc).isoformat(),
        "risk": risk,
        "candidate": _serializable(candidate_to_dict(candidate)),
    }


def append_review_candidates(candidates: list[object], path: Path = DEFAULT_REVIEW_FILE, *, source: str = "monitor") -> list[dict[str, object]]:
    records = [build_review_record(candidate, source=source) for candidate in candidates]
    if not records:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
    return records


def load_review_candidates(path: Path = DEFAULT_REVIEW_FILE, *, limit: int = 100) -> list[dict[str, object]]:
    rows = _read_jsonl(path)
    return rows[-limit:] if limit > 0 else rows


def append_review_label(review_id: str, label: str, path: Path = DEFAULT_LABEL_FILE, *, actor: str | None = None) -> dict[str, object]:
    if label not in {"same_event", "different_event", "unsure"}:
        raise ValueError("Unknown review label.")
    record = {
        "type": "review_label",
        "review_id": review_id,
        "label": label,
        "actor": actor,
        "labeled_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False))
        handle.write("\n")
    return record


def load_review_labels(path: Path = DEFAULT_LABEL_FILE) -> dict[str, dict[str, object]]:
    labels = {}
    for row in _read_jsonl(path):
        review_id = str(row.get("review_id") or "")
        if review_id:
            labels[review_id] = row
    return labels


def load_review_queue(candidate_path: Path = DEFAULT_REVIEW_FILE, label_path: Path = DEFAULT_LABEL_FILE, *, limit: int = 100) -> list[dict[str, object]]:
    labels = load_review_labels(label_path)
    rows = []
    for row in load_review_candidates(candidate_path, limit=limit):
        review_id = str(row.get("review_id") or "")
        row = dict(row)
        row["label"] = labels.get(review_id)
        rows.append(row)
    return rows


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _serializable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serializable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
