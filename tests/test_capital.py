from __future__ import annotations

import unittest

from prediction_arb.capital import parse_balances, parse_inventory, plan_capital


def opportunity(buy_source: str, sell_source: str, notional: float, profit: float) -> dict:
    return {
        "outcome": "YES",
        "buy_source": buy_source,
        "buy_market_id": "buy",
        "buy_title": "Buy",
        "sell_source": sell_source,
        "sell_market_id": "sell",
        "sell_title": "Sell",
        "net_edge": profit / 100,
        "executable_size": 100,
        "buy_quote": {"notional": notional},
        "sell_quote": {"filled_size": 100},
    }


class CapitalTests(unittest.TestCase):
    def test_parse_balances(self) -> None:
        self.assertEqual(parse_balances("limitless=10,polymarket=20"), {"limitless": 10.0, "polymarket": 20.0})

    def test_parse_inventory(self) -> None:
        self.assertEqual(parse_inventory("polymarket:YES:123=50"), {"polymarket:YES:123": 50.0})

    def test_plan_allocates_by_profit_and_cash(self) -> None:
        rows = [opportunity("limitless", "polymarket", 90, 1), opportunity("limitless", "polymarket", 80, 3)]

        plan = plan_capital(rows, {"limitless": 100, "polymarket": 0})

        self.assertEqual(plan["allocated_count"], 1)
        self.assertEqual(plan["allocated"][0]["estimated_profit"], 3.0)
        self.assertEqual(plan["rejected"][0]["planner_rejection_reason"], "insufficient_buy_cash")

    def test_plan_can_require_sell_inventory(self) -> None:
        rows = [opportunity("limitless", "polymarket", 10, 1)]

        plan = plan_capital(rows, {"limitless": 100}, assume_sell_inventory=False)

        self.assertEqual(plan["allocated_count"], 0)
        self.assertEqual(plan["rejected"][0]["planner_rejection_reason"], "insufficient_sell_inventory")


if __name__ == "__main__":
    unittest.main()
