from __future__ import annotations

import json
from pathlib import Path


def summarize_monitor_history(path: Path, top: int = 10) -> dict:
    snapshots = read_monitor_history(path)
    error_snapshots = [item for item in snapshots if item.get("type") == "error"]
    success_snapshots = [item for item in snapshots if item.get("type") != "error"]
    best_by_key: dict[str, dict] = {}
    total_new = 0
    total_gone = 0

    for snapshot in success_snapshots:
        total_new += int(snapshot.get("new_count") or 0)
        total_gone += int(snapshot.get("gone_count") or 0)
        for item in snapshot.get("opportunities", []):
            key = _opportunity_key_from_payload(item)
            if key is None:
                continue
            current = _opportunity_summary(item, snapshot)
            previous = best_by_key.get(key)
            if previous is None or (current["net_edge"] or -999.0) > (previous["net_edge"] or -999.0):
                best_by_key[key] = current

    latest_success = success_snapshots[-1] if success_snapshots else {}
    latest = snapshots[-1] if snapshots else {}
    best_routes = sorted(
        best_by_key.values(),
        key=lambda item: item["net_edge"] if item["net_edge"] is not None else -999.0,
        reverse=True,
    )
    return {
        "input": str(path),
        "snapshots": len(snapshots),
        "successful_snapshots": len(success_snapshots),
        "error_snapshots": len(error_snapshots),
        "first_detected_at": snapshots[0].get("detected_at") if snapshots else None,
        "last_detected_at": latest.get("detected_at"),
        "last_success_detected_at": latest_success.get("detected_at"),
        "last_error": error_snapshots[-1].get("error") if error_snapshots else None,
        "total_new_events": total_new,
        "total_gone_events": total_gone,
        "latest_active_count": latest_success.get("opportunity_count", 0),
        "latest_active_keys": latest_success.get("active_keys", []),
        "unique_routes_seen": len(best_by_key),
        "best_routes": best_routes[:top],
    }


def read_monitor_history(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _opportunity_key_from_payload(item: dict) -> str | None:
    required = ["outcome", "buy_source", "buy_market_id", "sell_source", "sell_market_id"]
    if any(item.get(field) in (None, "") for field in required):
        return None
    return "|".join(str(item[field]) for field in required)


def _opportunity_summary(item: dict, snapshot: dict) -> dict:
    net_edge = _float(item.get("net_edge"))
    executable_size = _float(item.get("executable_size")) or 0.0
    return {
        "key": _opportunity_key_from_payload(item),
        "outcome": item.get("outcome"),
        "route": f"{item.get('buy_source')}->{item.get('sell_source')}",
        "buy_title": item.get("buy_title"),
        "sell_title": item.get("sell_title"),
        "net_edge": net_edge,
        "executable_size": executable_size,
        "estimated_profit": net_edge * executable_size if net_edge is not None else None,
        "fee_estimate": item.get("fee_estimate"),
        "fee_notes": item.get("fee_notes", []),
        "detected_at": item.get("detected_at") or snapshot.get("detected_at"),
        "buy_url": item.get("buy_url"),
        "sell_url": item.get("sell_url"),
    }


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
