from __future__ import annotations

from copy import deepcopy


def plan_capital(
    opportunities: list[dict],
    cash: dict[str, float],
    inventory: dict[str, float] | None = None,
    assume_sell_inventory: bool = True,
    max_allocations: int = 10,
) -> dict:
    inventory = dict(inventory or {})
    remaining_cash = {str(source): float(amount) for source, amount in cash.items()}
    remaining_inventory = {str(key): float(amount) for key, amount in inventory.items()}
    allocated = []
    rejected = []

    ranked = sorted(
        opportunities,
        key=lambda item: _estimated_profit(item),
        reverse=True,
    )
    for item in ranked:
        if len(allocated) >= max_allocations:
            rejected.append(_rejection(item, "allocation_limit_reached", remaining_cash, remaining_inventory))
            continue

        buy_source = str(item.get("buy_source") or "")
        sell_source = str(item.get("sell_source") or "")
        outcome = str(item.get("outcome") or "")
        buy_cash_required = _quote_notional(item.get("buy_quote"))
        sell_inventory_required = _quote_filled_size(item.get("sell_quote"))
        inventory_key = _inventory_key(sell_source, outcome, item.get("sell_market_id"))

        if remaining_cash.get(buy_source, 0.0) < buy_cash_required:
            rejected.append(_rejection(item, "insufficient_buy_cash", remaining_cash, remaining_inventory))
            continue

        if not assume_sell_inventory and remaining_inventory.get(inventory_key, 0.0) < sell_inventory_required:
            rejected.append(_rejection(item, "insufficient_sell_inventory", remaining_cash, remaining_inventory))
            continue

        remaining_cash[buy_source] = remaining_cash.get(buy_source, 0.0) - buy_cash_required
        if not assume_sell_inventory:
            remaining_inventory[inventory_key] = remaining_inventory.get(inventory_key, 0.0) - sell_inventory_required

        allocated.append(
            {
                "key": _opportunity_key(item),
                "outcome": outcome,
                "route": f"{buy_source}->{sell_source}",
                "buy_title": item.get("buy_title"),
                "sell_title": item.get("sell_title"),
                "buy_cash_required": buy_cash_required,
                "sell_inventory_key": inventory_key,
                "sell_inventory_required": sell_inventory_required,
                "net_edge": _float(item.get("net_edge")) or 0.0,
                "estimated_profit": _estimated_profit(item),
                "fee_estimate": item.get("fee_estimate"),
                "fee_notes": item.get("fee_notes", []),
                "buy_url": item.get("buy_url"),
                "sell_url": item.get("sell_url"),
            }
        )

    return {
        "cash_start": {str(source): float(amount) for source, amount in cash.items()},
        "cash_remaining": remaining_cash,
        "inventory_start": dict(inventory),
        "inventory_remaining": remaining_inventory,
        "assume_sell_inventory": assume_sell_inventory,
        "allocated_count": len(allocated),
        "rejected_count": len(rejected),
        "total_buy_cash_required": sum(item["buy_cash_required"] for item in allocated),
        "total_estimated_profit": sum(item["estimated_profit"] for item in allocated),
        "allocated": allocated,
        "rejected": rejected,
    }


def parse_balances(value: str) -> dict[str, float]:
    balances: dict[str, float] = {}
    if not value:
        return balances
    for item in value.split(","):
        if not item.strip():
            continue
        key, amount = item.split("=", 1)
        balances[key.strip()] = float(amount)
    return balances


def parse_inventory(value: str) -> dict[str, float]:
    inventory: dict[str, float] = {}
    if not value:
        return inventory
    for item in value.split(","):
        if not item.strip():
            continue
        key, amount = item.rsplit("=", 1)
        inventory[key.strip()] = float(amount)
    return inventory


def _rejection(item: dict, reason: str, remaining_cash: dict[str, float], remaining_inventory: dict[str, float]) -> dict:
    row = deepcopy(item)
    row["planner_rejection_reason"] = reason
    row["buy_cash_required"] = _quote_notional(item.get("buy_quote"))
    row["sell_inventory_required"] = _quote_filled_size(item.get("sell_quote"))
    row["sell_inventory_key"] = _inventory_key(str(item.get("sell_source") or ""), str(item.get("outcome") or ""), item.get("sell_market_id"))
    row["cash_remaining_at_rejection"] = dict(remaining_cash)
    row["inventory_remaining_at_rejection"] = dict(remaining_inventory)
    return row


def _opportunity_key(item: dict) -> str:
    return "|".join(
        str(item.get(field) or "")
        for field in ["outcome", "buy_source", "buy_market_id", "sell_source", "sell_market_id"]
    )


def _inventory_key(source: str, outcome: str, market_id: object) -> str:
    return f"{source}:{outcome}:{market_id}"


def _estimated_profit(item: dict) -> float:
    net_edge = _float(item.get("net_edge")) or 0.0
    executable_size = _float(item.get("executable_size")) or 0.0
    return net_edge * executable_size


def _quote_notional(value: object) -> float:
    return _float(value.get("notional")) if isinstance(value, dict) and _float(value.get("notional")) is not None else 0.0


def _quote_filled_size(value: object) -> float:
    return _float(value.get("filled_size")) if isinstance(value, dict) and _float(value.get("filled_size")) is not None else 0.0


def _float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
