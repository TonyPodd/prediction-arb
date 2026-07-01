from __future__ import annotations

from pathlib import Path

from prediction_arb.capital import plan_capital
from prediction_arb.portfolio import load_portfolio, now_iso, portfolio_summary, save_portfolio
from prediction_arb.reporting import latest_opportunities


def paper_enter_from_monitor(
    monitor_path: Path,
    portfolio_path: Path,
    max_allocations: int = 5,
    require_sell_inventory: bool = False,
) -> dict:
    portfolio = load_portfolio(portfolio_path)
    existing_keys = {item.get("key") for item in portfolio.get("open_positions", [])}
    opportunities = [item for item in latest_opportunities(monitor_path) if _opportunity_key(item) not in existing_keys]
    plan = plan_capital(
        opportunities,
        portfolio.get("cash", {}),
        portfolio.get("inventory", {}),
        assume_sell_inventory=not require_sell_inventory,
        max_allocations=max_allocations,
    )
    entered = []
    for item in plan["allocated"]:
        buy_source = item["route"].split("->", 1)[0]
        portfolio["cash"][buy_source] = float(portfolio["cash"].get(buy_source, 0.0)) - item["buy_cash_required"]
        if require_sell_inventory:
            key = item["sell_inventory_key"]
            portfolio["inventory"][key] = float(portfolio["inventory"].get(key, 0.0)) - item["sell_inventory_required"]
        position = {
            "key": item["key"],
            "opened_at": now_iso(),
            "status": "open",
            "outcome": item["outcome"],
            "route": item["route"],
            "buy_title": item.get("buy_title"),
            "sell_title": item.get("sell_title"),
            "buy_cash_required": item["buy_cash_required"],
            "sell_inventory_key": item["sell_inventory_key"],
            "sell_inventory_required": item["sell_inventory_required"],
            "entry_net_edge": item["net_edge"],
            "entry_estimated_profit": item["estimated_profit"],
            "buy_url": item.get("buy_url"),
            "sell_url": item.get("sell_url"),
        }
        portfolio["open_positions"].append(position)
        entered.append(position)

    for item in plan["rejected"]:
        portfolio["rejected"].append(
            {
                "at": now_iso(),
                "key": _opportunity_key(item),
                "reason": item.get("planner_rejection_reason"),
                "buy_cash_required": item.get("buy_cash_required"),
                "sell_inventory_required": item.get("sell_inventory_required"),
            }
        )

    save_portfolio(portfolio_path, portfolio)
    return {
        "entered_count": len(entered),
        "entered": entered,
        "plan": plan,
        "portfolio": portfolio_summary(portfolio),
    }


def paper_mark_close(portfolio_path: Path, key: str, realized_pnl: float = 0.0) -> dict:
    portfolio = load_portfolio(portfolio_path)
    remaining = []
    closed = None
    for position in portfolio.get("open_positions", []):
        if position.get("key") == key and closed is None:
            closed = dict(position)
            closed["status"] = "closed"
            closed["closed_at"] = now_iso()
            closed["realized_pnl"] = float(realized_pnl)
            portfolio["closed_positions"].append(closed)
        else:
            remaining.append(position)
    portfolio["open_positions"] = remaining
    save_portfolio(portfolio_path, portfolio)
    return {"closed": closed, "portfolio": portfolio_summary(portfolio)}


def paper_sync_from_monitor(monitor_path: Path, portfolio_path: Path) -> dict:
    portfolio = load_portfolio(portfolio_path)
    latest_by_key = {_opportunity_key(item): item for item in latest_opportunities(monitor_path)}
    updated = []
    stale = []
    for position in portfolio.get("open_positions", []):
        current = latest_by_key.get(position.get("key"))
        position["last_checked_at"] = now_iso()
        if current is None:
            position["market_status"] = "not_in_latest_snapshot"
            stale.append(position)
            continue
        current_net_edge = float(current.get("net_edge") or 0.0)
        current_size = float(current.get("executable_size") or 0.0)
        position["market_status"] = "active"
        position["current_net_edge"] = current_net_edge
        position["current_estimated_profit"] = current_net_edge * current_size
        position["current_executable_size"] = current_size
        position["edge_change"] = current_net_edge - float(position.get("entry_net_edge") or 0.0)
        updated.append(position)

    save_portfolio(portfolio_path, portfolio)
    return {
        "updated_count": len(updated),
        "stale_count": len(stale),
        "updated": updated,
        "stale": stale,
        "portfolio": portfolio_summary(portfolio),
    }


def _opportunity_key(item: dict) -> str:
    return "|".join(
        str(item.get(field) or "")
        for field in ["outcome", "buy_source", "buy_market_id", "sell_source", "sell_market_id"]
    )
