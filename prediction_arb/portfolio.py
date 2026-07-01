from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_PORTFOLIO = {
    "cash": {"limitless": 250.0, "polymarket": 250.0},
    "inventory": {},
    "open_positions": [],
    "closed_positions": [],
    "rejected": [],
}


def load_portfolio(path: Path) -> dict:
    if not path.exists():
        return _copy_default()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    portfolio = _copy_default()
    portfolio.update(data if isinstance(data, dict) else {})
    portfolio["cash"] = {str(key): float(value) for key, value in (portfolio.get("cash") or {}).items()}
    portfolio["inventory"] = {str(key): float(value) for key, value in (portfolio.get("inventory") or {}).items()}
    portfolio["open_positions"] = list(portfolio.get("open_positions") or [])
    portfolio["closed_positions"] = list(portfolio.get("closed_positions") or [])
    portfolio["rejected"] = list(portfolio.get("rejected") or [])
    return portfolio


def save_portfolio(path: Path, portfolio: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(portfolio, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def initialize_portfolio(path: Path, cash: dict[str, float], overwrite: bool = False) -> dict:
    if path.exists() and not overwrite:
        return load_portfolio(path)
    portfolio = _copy_default()
    portfolio["cash"] = {str(source): float(amount) for source, amount in cash.items()}
    save_portfolio(path, portfolio)
    return portfolio


def portfolio_summary(portfolio: dict) -> dict:
    open_positions = list(portfolio.get("open_positions") or [])
    closed_positions = list(portfolio.get("closed_positions") or [])
    return {
        "cash": dict(portfolio.get("cash") or {}),
        "inventory": dict(portfolio.get("inventory") or {}),
        "open_count": len(open_positions),
        "closed_count": len(closed_positions),
        "rejected_count": len(portfolio.get("rejected") or []),
        "open_notional": sum(float(item.get("buy_cash_required") or 0.0) for item in open_positions),
        "open_estimated_profit": sum(float(item.get("entry_estimated_profit") or 0.0) for item in open_positions),
        "current_estimated_profit": sum(float(item.get("current_estimated_profit") or item.get("entry_estimated_profit") or 0.0) for item in open_positions),
        "realized_pnl": sum(float(item.get("realized_pnl") or 0.0) for item in closed_positions),
        "open_positions": open_positions,
        "closed_positions": closed_positions,
    }


def _copy_default() -> dict:
    return {
        "cash": dict(DEFAULT_PORTFOLIO["cash"]),
        "inventory": dict(DEFAULT_PORTFOLIO["inventory"]),
        "open_positions": [],
        "closed_positions": [],
        "rejected": [],
    }


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
