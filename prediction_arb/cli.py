from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

from prediction_arb.capital import parse_balances, parse_inventory, plan_capital
from prediction_arb.coverage import summarize_source_coverage
from prediction_arb.dashboard import serve_dashboard
from prediction_arb.depth import estimate_market_taker_fee_per_share, find_max_depth_size, scan_depth_candidates, sweep_depth
from prediction_arb.diagnostics import build_health_report
from prediction_arb.matching import market_match_details
from prediction_arb.monitor import _opportunity_key, build_telegram_payload, build_webhook_payload, format_new_opportunity_alert, monitor_once
from prediction_arb.near import append_near_opportunities, select_near_opportunities
from prediction_arb.paper import paper_enter_from_monitor, paper_mark_close, paper_sync_from_monitor, run_paper_loop
from prediction_arb.portfolio import initialize_portfolio, load_portfolio, portfolio_summary
from prediction_arb.reporting import latest_opportunities, summarize_monitor_history
from prediction_arb.research import DEFAULT_RESEARCH_FILE, build_research_snapshot
from prediction_arb.review_analysis import summarize_review_quality
from prediction_arb.review_store import append_review_candidates
from prediction_arb.risk import assess_candidate_risk
from prediction_arb.scanner import scan_opportunities
from prediction_arb.sources import kalshi, limitless, polymarket
from prediction_arb.telegram_bot import run_telegram_bot


SEARCH_PRESETS = {
    "short-term": [
        "btc",
        "eth",
        "sol",
        "xrp",
        "doge",
        "world cup",
        "wimbledon tennis",
        "tennis match",
        "lol esports",
        "cs2 esports",
        "mlb baseball",
    ],
}


def main() -> None:
    _load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(prog="prediction-arb")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan Kalshi and Polymarket for potential gaps.")
    scan_parser.add_argument("--limit", type=int, default=100)
    scan_parser.add_argument("--min-edge", type=float, default=0.02)
    scan_parser.add_argument("--min-match-score", type=float, default=0.25)
    scan_parser.add_argument("--min-liquidity", type=float, default=0.0)
    scan_parser.add_argument("--query")
    scan_parser.add_argument("--output", type=Path)

    markets_parser = subparsers.add_parser("markets", help="Dump normalized markets from one source.")
    markets_parser.add_argument("--source", choices=["kalshi", "limitless", "polymarket"], required=True)
    markets_parser.add_argument("--limit", type=int, default=20)
    markets_parser.add_argument("--output", type=Path)
    markets_parser.add_argument("--include-raw", action="store_true")

    candidates_parser = subparsers.add_parser("candidates", help="Show possible matching markets before scanning edge.")
    candidates_parser.add_argument("--query", required=True)
    candidates_parser.add_argument("--limit", type=int, default=100)
    candidates_parser.add_argument("--min-match-score", type=float, default=0.15)
    candidates_parser.add_argument("--max-results", type=int, default=25)
    candidates_parser.add_argument("--output", type=Path)

    diagnose_parser = subparsers.add_parser("diagnose", help="Summarize matching quality for one or more topics.")
    diagnose_parser.add_argument("--query", action="append", required=True)
    diagnose_parser.add_argument("--limit", type=int, default=50)
    diagnose_parser.add_argument("--min-match-score", type=float, default=0.05)
    diagnose_parser.add_argument("--min-edge", type=float, default=0.005)
    diagnose_parser.add_argument("--output", type=Path)

    coverage_parser = subparsers.add_parser("coverage", help="Summarize fetched source coverage by kind, asset, interval, and close window.")
    coverage_parser.add_argument("--query", action="append", default=[])
    coverage_parser.add_argument("--preset", choices=sorted(SEARCH_PRESETS), action="append", default=[])
    coverage_parser.add_argument("--category", action="append", default=[])
    coverage_parser.add_argument("--all-markets", action="store_true")
    coverage_parser.add_argument("--limit", type=int, default=100)
    coverage_parser.add_argument("--min-close-minutes", type=float)
    coverage_parser.add_argument("--max-close-hours", type=float)
    coverage_parser.add_argument("--examples", type=int, default=8)
    coverage_parser.add_argument("--output", type=Path)

    health_parser = subparsers.add_parser("health", help="Diagnose source coverage, matching funnel, depth, fees, and operational costs.")
    health_parser.add_argument("--query", action="append", default=[])
    health_parser.add_argument("--preset", choices=sorted(SEARCH_PRESETS), action="append", default=[])
    health_parser.add_argument("--category", action="append", default=[])
    health_parser.add_argument("--all-markets", action="store_true")
    health_parser.add_argument("--limit", type=int, default=300)
    health_parser.add_argument("--size", type=float, default=100.0)
    health_parser.add_argument("--min-net-edge", type=float, default=0.005)
    health_parser.add_argument("--min-profit", type=float, default=1.0)
    health_parser.add_argument("--safety-buffer", type=float, default=0.002)
    health_parser.add_argument("--fee-bps", type=float, default=50.0)
    health_parser.add_argument("--route-fixed-cost", default="*=2")
    health_parser.add_argument("--route-cost-bps", default="*=25")
    health_parser.add_argument("--min-match-score", type=float, default=0.25)
    health_parser.add_argument("--max-depth-pairs", type=int, default=40)
    health_parser.add_argument("--min-close-minutes", type=float)
    health_parser.add_argument("--max-close-hours", type=float, default=24.0)
    health_parser.add_argument("--output", type=Path)

    depth_parser = subparsers.add_parser("depth-scan", help="Scan executable edge using orderbook depth.")
    depth_parser.add_argument("--query", required=True)
    depth_parser.add_argument("--limit", type=int, default=50)
    depth_parser.add_argument("--size", type=float, default=100.0)
    depth_parser.add_argument("--min-net-edge", type=float, default=0.005)
    depth_parser.add_argument("--min-profit", type=float, default=0.0)
    depth_parser.add_argument("--safety-buffer", type=float, default=0.002)
    depth_parser.add_argument("--fee-bps", type=float, default=0.0)
    depth_parser.add_argument("--route-fixed-cost", default="", help="Fixed USDC costs per route, e.g. 'polymarket->kalshi=2,*=1'.")
    depth_parser.add_argument("--route-cost-bps", default="", help="Route operational bps on buy+sell notional, e.g. '*=25'.")
    depth_parser.add_argument("--min-match-score", type=float, default=0.25)
    depth_parser.add_argument("--allow-partial", action="store_true")
    depth_parser.add_argument("--include-filtered", action="store_true")
    depth_parser.add_argument("--output", type=Path)

    near_parser = subparsers.add_parser("near-misses", help="Show best rejected depth candidates with rejection reasons.")
    near_parser.add_argument("--query", action="append", default=[])
    near_parser.add_argument("--preset", choices=sorted(SEARCH_PRESETS), action="append", default=[])
    near_parser.add_argument("--category", action="append", default=[])
    near_parser.add_argument("--all-markets", action="store_true")
    near_parser.add_argument("--limit", type=int, default=200)
    near_parser.add_argument("--size", type=float, default=100.0)
    near_parser.add_argument("--min-net-edge", type=float, default=0.005)
    near_parser.add_argument("--min-profit", type=float, default=0.0)
    near_parser.add_argument("--safety-buffer", type=float, default=0.002)
    near_parser.add_argument("--fee-bps", type=float, default=0.0)
    near_parser.add_argument("--route-fixed-cost", default="")
    near_parser.add_argument("--route-cost-bps", default="")
    near_parser.add_argument("--min-match-score", type=float, default=0.25)
    near_parser.add_argument("--min-close-minutes", type=float)
    near_parser.add_argument("--max-close-hours", type=float)
    near_parser.add_argument("--top", type=int, default=20)
    near_parser.add_argument("--output", type=Path)

    near_opps_parser = subparsers.add_parser("near-opportunities", help="Show positive-edge candidates rejected by profit/cost filters.")
    near_opps_parser.add_argument("--query", action="append", default=[])
    near_opps_parser.add_argument("--preset", choices=sorted(SEARCH_PRESETS), action="append", default=[])
    near_opps_parser.add_argument("--category", action="append", default=[])
    near_opps_parser.add_argument("--all-markets", action="store_true")
    near_opps_parser.add_argument("--limit", type=int, default=300)
    near_opps_parser.add_argument("--size", type=float, default=100.0)
    near_opps_parser.add_argument("--min-net-edge", type=float, default=0.005)
    near_opps_parser.add_argument("--min-profit", type=float, default=1.0)
    near_opps_parser.add_argument("--safety-buffer", type=float, default=0.002)
    near_opps_parser.add_argument("--fee-bps", type=float, default=50.0)
    near_opps_parser.add_argument("--route-fixed-cost", default="*=2")
    near_opps_parser.add_argument("--route-cost-bps", default="*=25")
    near_opps_parser.add_argument("--min-match-score", type=float, default=0.25)
    near_opps_parser.add_argument("--min-close-minutes", type=float)
    near_opps_parser.add_argument("--max-close-hours", type=float, default=24.0)
    near_opps_parser.add_argument("--near-min-edge", type=float, default=0.0)
    near_opps_parser.add_argument("--top", type=int, default=20)
    near_opps_parser.add_argument("--save", action="store_true")
    near_opps_parser.add_argument("--near-output", type=Path, default=Path("data/near-opportunities.jsonl"))
    near_opps_parser.add_argument("--output", type=Path)

    sweep_parser = subparsers.add_parser("depth-sweep", help="Run depth scan across multiple share sizes.")
    sweep_parser.add_argument("--query", required=True)
    sweep_parser.add_argument("--limit", type=int, default=50)
    sweep_parser.add_argument("--sizes", default="10,50,100,250,500,1000")
    sweep_parser.add_argument("--min-net-edge", type=float, default=0.005)
    sweep_parser.add_argument("--safety-buffer", type=float, default=0.002)
    sweep_parser.add_argument("--fee-bps", type=float, default=0.0)
    sweep_parser.add_argument("--route-fixed-cost", default="")
    sweep_parser.add_argument("--route-cost-bps", default="")
    sweep_parser.add_argument("--min-match-score", type=float, default=0.25)
    sweep_parser.add_argument("--output", type=Path)

    max_parser = subparsers.add_parser("depth-max", help="Find the largest passing depth size on a geometric grid.")
    max_parser.add_argument("--query", required=True)
    max_parser.add_argument("--limit", type=int, default=50)
    max_parser.add_argument("--min-size", type=float, default=10.0)
    max_parser.add_argument("--max-size", type=float, default=10_000.0)
    max_parser.add_argument("--step-multiplier", type=float, default=2.0)
    max_parser.add_argument("--min-net-edge", type=float, default=0.005)
    max_parser.add_argument("--safety-buffer", type=float, default=0.002)
    max_parser.add_argument("--fee-bps", type=float, default=0.0)
    max_parser.add_argument("--route-fixed-cost", default="")
    max_parser.add_argument("--route-cost-bps", default="")
    max_parser.add_argument("--min-match-score", type=float, default=0.25)
    max_parser.add_argument("--output", type=Path)

    fees_parser = subparsers.add_parser("fees", help="Show fee assumptions for matched source markets.")
    fees_parser.add_argument("--query", required=True)
    fees_parser.add_argument("--limit", type=int, default=20)
    fees_parser.add_argument("--prices", default="0.05,0.5,0.95")
    fees_parser.add_argument("--output", type=Path)

    monitor_parser = subparsers.add_parser("monitor", help="Repeatedly scan depth opportunities and append snapshots to JSONL.")
    monitor_parser.add_argument("--query", action="append", default=[])
    monitor_parser.add_argument("--preset", choices=sorted(SEARCH_PRESETS), action="append", default=[], help="Append a bounded query preset, e.g. short-term.")
    monitor_parser.add_argument("--category", action="append", default=[], help="Filter fetched markets by category/tag/title text. Can be repeated.")
    monitor_parser.add_argument("--all-markets", action="store_true", help="Scan fetched source universes without query filtering.")
    monitor_parser.add_argument("--limit", type=int, default=50)
    monitor_parser.add_argument("--size", type=float, default=100.0)
    monitor_parser.add_argument("--interval", type=float, default=30.0)
    monitor_parser.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted.")
    monitor_parser.add_argument("--min-net-edge", type=float, default=0.005)
    monitor_parser.add_argument("--min-profit", type=float, default=0.0)
    monitor_parser.add_argument("--safety-buffer", type=float, default=0.002)
    monitor_parser.add_argument("--fee-bps", type=float, default=50.0)
    monitor_parser.add_argument("--route-fixed-cost", default="*=2", help="Fixed USDC operational costs per route, divided by executable size.")
    monitor_parser.add_argument("--route-cost-bps", default="*=25", help="Operational bps per route on buy+sell notional.")
    monitor_parser.add_argument("--min-match-score", type=float, default=0.25)
    monitor_parser.add_argument("--max-depth-pairs", type=int, default=40, help="Maximum structurally compatible pairs to fetch orderbooks for per iteration. 0 means no limit.")
    monitor_parser.add_argument("--min-close-minutes", type=float)
    monitor_parser.add_argument("--max-close-hours", type=float)
    monitor_parser.add_argument("--output", type=Path, default=Path("data/monitor.jsonl"))
    monitor_parser.add_argument("--print-snapshots", action="store_true")
    monitor_parser.add_argument("--alert-new", action="store_true", help="Print a compact alert when new opportunities appear.")
    monitor_parser.add_argument("--webhook-url", help="POST new-opportunity alerts to this webhook URL.")
    monitor_parser.add_argument("--webhook-format", choices=["generic", "discord"], default="generic")
    monitor_parser.add_argument("--telegram-bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"), help="Telegram bot token. Defaults to TELEGRAM_BOT_TOKEN.")
    monitor_parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"), help="Telegram chat id. Defaults to TELEGRAM_CHAT_ID.")
    monitor_parser.add_argument("--review-output", type=Path, default=Path("data/review-candidates.jsonl"), help="JSONL file for suspicious/manual-review candidates.")
    monitor_parser.add_argument("--save-suspicious", action="store_true", help="Append suspicious opportunities to the manual-review dataset.")
    monitor_parser.add_argument("--alert-suspicious", action="store_true", help="Send suspicious opportunities to Telegram for manual review.")
    monitor_parser.add_argument("--suspicious-min-risk", type=int, default=25)
    monitor_parser.add_argument("--stop-on-error", action="store_true", help="Exit instead of appending an error snapshot when a scan fails.")

    research_parser = subparsers.add_parser("research-monitor", help="Slow read-only 7-day monitor for near-opportunities and matcher research.")
    research_parser.add_argument("--query", action="append", default=[])
    research_parser.add_argument("--preset", choices=sorted(SEARCH_PRESETS), action="append", default=[])
    research_parser.add_argument("--category", action="append", default=[])
    research_parser.add_argument("--all-markets", action="store_true")
    research_parser.add_argument("--limit", type=int, default=500)
    research_parser.add_argument("--size", type=float, default=100.0)
    research_parser.add_argument("--interval", type=float, default=900.0)
    research_parser.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted.")
    research_parser.add_argument("--min-net-edge", type=float, default=0.005)
    research_parser.add_argument("--min-profit", type=float, default=1.0)
    research_parser.add_argument("--safety-buffer", type=float, default=0.002)
    research_parser.add_argument("--fee-bps", type=float, default=50.0)
    research_parser.add_argument("--route-fixed-cost", default="*=2")
    research_parser.add_argument("--route-cost-bps", default="*=25")
    research_parser.add_argument("--min-match-score", type=float, default=0.25)
    research_parser.add_argument("--min-close-minutes", type=float)
    research_parser.add_argument("--max-close-hours", type=float, default=168.0)
    research_parser.add_argument("--near-min-edge", type=float, default=0.0)
    research_parser.add_argument("--top", type=int, default=30)
    research_parser.add_argument("--max-depth-pairs", type=int, default=40, help="Maximum structurally compatible pairs to fetch orderbooks for per iteration. 0 means no limit.")
    research_parser.add_argument("--output", type=Path, default=DEFAULT_RESEARCH_FILE)
    research_parser.add_argument("--near-output", type=Path, default=Path("data/near-opportunities.jsonl"))
    research_parser.add_argument("--print-snapshots", action="store_true")
    research_parser.add_argument("--stop-on-error", action="store_true")

    report_parser = subparsers.add_parser("monitor-report", help="Summarize monitor JSONL history.")
    report_parser.add_argument("--input", type=Path, required=True)
    report_parser.add_argument("--top", type=int, default=10)
    report_parser.add_argument("--output", type=Path)

    review_report_parser = subparsers.add_parser("review-report", help="Summarize manual review labels for matcher quality.")
    review_report_parser.add_argument("--reviews", type=Path, default=Path("data/review-candidates.jsonl"))
    review_report_parser.add_argument("--labels", type=Path, default=Path("data/review-labels.jsonl"))
    review_report_parser.add_argument("--limit", type=int, default=0)
    review_report_parser.add_argument("--examples", type=int, default=10)
    review_report_parser.add_argument("--output", type=Path)

    capital_parser = subparsers.add_parser("capital-plan", help="Plan capital allocation across latest monitor opportunities.")
    capital_parser.add_argument("--input", type=Path, required=True)
    capital_parser.add_argument("--cash", default="kalshi=250,polymarket=250")
    capital_parser.add_argument("--inventory", default="")
    capital_parser.add_argument("--require-sell-inventory", action="store_true")
    capital_parser.add_argument("--max-allocations", type=int, default=10)
    capital_parser.add_argument("--output", type=Path)

    portfolio_init_parser = subparsers.add_parser("portfolio-init", help="Initialize local paper portfolio state.")
    portfolio_init_parser.add_argument("--portfolio", type=Path, default=Path("data/portfolio.json"))
    portfolio_init_parser.add_argument("--cash", default="kalshi=250,polymarket=250")
    portfolio_init_parser.add_argument("--overwrite", action="store_true")
    portfolio_init_parser.add_argument("--output", type=Path)

    portfolio_status_parser = subparsers.add_parser("portfolio-status", help="Show local paper portfolio state.")
    portfolio_status_parser.add_argument("--portfolio", type=Path, default=Path("data/portfolio.json"))
    portfolio_status_parser.add_argument("--output", type=Path)

    paper_enter_parser = subparsers.add_parser("paper-enter", help="Open paper positions from latest monitor opportunities.")
    paper_enter_parser.add_argument("--input", type=Path, required=True)
    paper_enter_parser.add_argument("--portfolio", type=Path, default=Path("data/portfolio.json"))
    paper_enter_parser.add_argument("--max-allocations", type=int, default=5)
    paper_enter_parser.add_argument("--require-sell-inventory", action="store_true")
    paper_enter_parser.add_argument("--output", type=Path)

    paper_close_parser = subparsers.add_parser("paper-close", help="Mark a paper position closed.")
    paper_close_parser.add_argument("--portfolio", type=Path, default=Path("data/portfolio.json"))
    paper_close_parser.add_argument("--key", required=True)
    paper_close_parser.add_argument("--realized-pnl", type=float, default=0.0)
    paper_close_parser.add_argument("--output", type=Path)

    paper_sync_parser = subparsers.add_parser("paper-sync", help="Mark open paper positions to latest monitor opportunities.")
    paper_sync_parser.add_argument("--input", type=Path, required=True)
    paper_sync_parser.add_argument("--portfolio", type=Path, default=Path("data/portfolio.json"))
    paper_sync_parser.add_argument("--output", type=Path)

    paper_loop_parser = subparsers.add_parser("paper-loop", help="Continuously sync and optionally enter paper positions.")
    paper_loop_parser.add_argument("--input", type=Path, required=True)
    paper_loop_parser.add_argument("--portfolio", type=Path, default=Path("data/portfolio.json"))
    paper_loop_parser.add_argument("--interval", type=float, default=60.0)
    paper_loop_parser.add_argument("--enter", action="store_true")
    paper_loop_parser.add_argument("--max-allocations", type=int, default=5)
    paper_loop_parser.add_argument("--require-sell-inventory", action="store_true")

    telegram_test_parser = subparsers.add_parser("telegram-test", help="Send a test Telegram message.")
    telegram_test_parser.add_argument("--bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    telegram_test_parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"))
    telegram_test_parser.add_argument("--message", default="prediction-arb Telegram alerts are configured")

    telegram_bot_parser = subparsers.add_parser("telegram-bot", help="Run Telegram command bot for monitor status/report.")
    telegram_bot_parser.add_argument("--bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    telegram_bot_parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"))
    telegram_bot_parser.add_argument("--input", type=Path, default=Path("data/monitor-kalshi-poly.jsonl"))
    telegram_bot_parser.add_argument("--poll-interval", type=float, default=2.0)

    dashboard_parser = subparsers.add_parser("dashboard", help="Serve local monitor dashboard.")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8765)
    dashboard_parser.add_argument("--input", type=Path, default=Path("data/monitor-kalshi-poly.jsonl"))

    args = parser.parse_args()
    if args.command == "scan":
        _scan(args)
    elif args.command == "markets":
        _markets(args)
    elif args.command == "candidates":
        _candidates(args)
    elif args.command == "diagnose":
        _diagnose(args)
    elif args.command == "coverage":
        _coverage(args)
    elif args.command == "health":
        _health(args)
    elif args.command == "depth-scan":
        _depth_scan(args)
    elif args.command == "near-misses":
        _near_misses(args)
    elif args.command == "near-opportunities":
        _near_opportunities(args)
    elif args.command == "depth-sweep":
        _depth_sweep(args)
    elif args.command == "depth-max":
        _depth_max(args)
    elif args.command == "fees":
        _fees(args)
    elif args.command == "monitor":
        _monitor(args)
    elif args.command == "research-monitor":
        _research_monitor(args)
    elif args.command == "monitor-report":
        _monitor_report(args)
    elif args.command == "review-report":
        _review_report(args)
    elif args.command == "capital-plan":
        _capital_plan(args)
    elif args.command == "portfolio-init":
        _portfolio_init(args)
    elif args.command == "portfolio-status":
        _portfolio_status(args)
    elif args.command == "paper-enter":
        _paper_enter(args)
    elif args.command == "paper-close":
        _paper_close(args)
    elif args.command == "paper-sync":
        _paper_sync(args)
    elif args.command == "paper-loop":
        _paper_loop(args)
    elif args.command == "telegram-test":
        _telegram_test(args)
    elif args.command == "telegram-bot":
        _telegram_bot(args)
    elif args.command == "dashboard":
        _dashboard(args)


def _scan(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_kalshi(args.limit, args.query or "")
    polymarket_markets = _fetch_polymarket(args.limit, args.query or "")
    opportunities = scan_opportunities(
        _filter_markets(limitless_markets, args.min_liquidity),
        _filter_markets(polymarket_markets, args.min_liquidity),
        min_edge=args.min_edge,
        min_match_score=args.min_match_score,
    )
    payload = [_serializable(asdict(item)) for item in opportunities]
    _write_or_print(payload, args.output)


def _markets(args: argparse.Namespace) -> None:
    if args.source == "kalshi":
        markets = kalshi.fetch_markets(limit=args.limit)
    elif args.source == "limitless":
        markets = limitless.fetch_markets(limit=args.limit)
    else:
        markets = polymarket.fetch_markets(limit=args.limit)
    payload = [_serializable(_market_dict(item, include_raw=args.include_raw)) for item in markets]
    _write_or_print(payload, args.output)


def _candidates(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_kalshi(args.limit, args.query)
    polymarket_markets = _fetch_polymarket(args.limit, args.query)
    rows = []

    for left in limitless_markets:
        for right in polymarket_markets:
            details = market_match_details(left, right)
            if details.score < args.min_match_score:
                continue
            rows.append(
                {
                    "match_score": details.score,
                    "shared_tokens": details.shared_tokens,
                    "warnings": details.warnings,
                    "condition_kinds": {
                        "limitless": details.left_condition_kind,
                        "polymarket": details.right_condition_kind,
                    },
                    "conditions": {
                        "limitless": asdict(details.left_condition) if details.left_condition else None,
                        "polymarket": asdict(details.right_condition) if details.right_condition else None,
                    },
                    "limitless": _candidate_market(left),
                    "polymarket": _candidate_market(right),
                }
            )

    rows.sort(key=lambda item: item["match_score"], reverse=True)
    _write_or_print(rows[: args.max_results], args.output)


def _diagnose(args: argparse.Namespace) -> None:
    rows = []
    for query in args.query:
        limitless_markets = _fetch_kalshi(args.limit, query)
        polymarket_markets = _fetch_polymarket(args.limit, query)
        pair_count = 0
        candidate_count = 0
        structurally_compatible_count = 0
        warning_counts: dict[str, int] = {}

        for left in limitless_markets:
            for right in polymarket_markets:
                pair_count += 1
                details = market_match_details(left, right)
                if details.score < args.min_match_score:
                    continue
                candidate_count += 1
                for warning in details.warnings:
                    warning_counts[warning] = warning_counts.get(warning, 0) + 1
                if not _has_hard_warnings(details.warnings):
                    structurally_compatible_count += 1

        opportunities = scan_opportunities(
            limitless_markets,
            polymarket_markets,
            min_edge=args.min_edge,
            min_match_score=args.min_match_score,
        )
        rows.append(
            {
                "query": query,
                "limitless_markets": len(limitless_markets),
                "polymarket_markets": len(polymarket_markets),
                "pairs_checked": pair_count,
                "text_candidates": candidate_count,
                "structurally_compatible_candidates": structurally_compatible_count,
                "opportunities": len(opportunities),
                "top_opportunity": _serializable(asdict(opportunities[0])) if opportunities else None,
                "warning_counts": dict(sorted(warning_counts.items())),
            }
        )

    _write_or_print(rows, args.output)


def _coverage(args: argparse.Namespace) -> None:
    if not _expanded_queries(args) and not args.category:
        args.all_markets = True
    limitless_markets, polymarket_markets = _fetch_monitor_universe(args)
    payload = summarize_source_coverage(
        limitless_markets,
        polymarket_markets,
        example_limit=args.examples,
    )
    payload["scope"] = _monitor_scope_label(args)
    _write_or_print(payload, args.output)


def _health(args: argparse.Namespace) -> None:
    if not _expanded_queries(args) and not args.category:
        args.all_markets = True
    limitless_markets, polymarket_markets = _fetch_monitor_universe(args)
    payload = build_health_report(
        limitless_markets,
        polymarket_markets,
        size=args.size,
        min_match_score=args.min_match_score,
        min_net_edge=args.min_net_edge,
        safety_buffer=args.safety_buffer,
        fee_bps=args.fee_bps,
        route_fixed_costs=_parse_cost_map(args.route_fixed_cost),
        route_cost_bps=_parse_cost_map(args.route_cost_bps),
        min_profit=args.min_profit,
        max_depth_pairs=args.max_depth_pairs,
    )
    payload["scope"] = _monitor_scope_label(args)
    _write_or_print([payload], args.output)


def _depth_scan(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_kalshi(args.limit, args.query)
    polymarket_markets = _fetch_polymarket(args.limit, args.query)
    rows = scan_depth_candidates(
        limitless_markets,
        polymarket_markets,
        size=args.size,
        min_net_edge=args.min_net_edge,
        safety_buffer=args.safety_buffer,
        min_match_score=args.min_match_score,
        allow_partial=args.allow_partial,
        fee_bps=args.fee_bps,
        min_profit=getattr(args, "min_profit", 0.0),
        route_fixed_costs=_parse_cost_map(args.route_fixed_cost),
        route_cost_bps=_parse_cost_map(args.route_cost_bps),
        include_filtered=args.include_filtered,
    )
    payload = [_serializable(asdict(item)) for item in rows]
    _write_or_print(payload, args.output)


def _near_misses(args: argparse.Namespace) -> None:
    if not args.query and not args.category:
        args.all_markets = True
    limitless_markets, polymarket_markets = _fetch_monitor_universe(args)
    rows = scan_depth_candidates(
        limitless_markets,
        polymarket_markets,
        size=args.size,
        min_net_edge=args.min_net_edge,
        safety_buffer=args.safety_buffer,
        min_match_score=args.min_match_score,
        allow_partial=False,
        fee_bps=args.fee_bps,
        min_profit=args.min_profit,
        route_fixed_costs=_parse_cost_map(args.route_fixed_cost),
        route_cost_bps=_parse_cost_map(args.route_cost_bps),
        include_filtered=True,
    )
    rejected = [row for row in rows if row.rejection_reason]
    rejected.sort(key=_near_miss_sort_key, reverse=True)
    payload = [_serializable(asdict(item)) for item in rejected[: args.top]]
    _write_or_print(payload, args.output)


def _near_opportunities(args: argparse.Namespace) -> None:
    if not args.query and not args.category:
        args.all_markets = True
    limitless_markets, polymarket_markets = _fetch_monitor_universe(args)
    rows = scan_depth_candidates(
        limitless_markets,
        polymarket_markets,
        size=args.size,
        min_net_edge=args.min_net_edge,
        safety_buffer=args.safety_buffer,
        min_match_score=args.min_match_score,
        allow_partial=False,
        fee_bps=args.fee_bps,
        min_profit=args.min_profit,
        route_fixed_costs=_parse_cost_map(args.route_fixed_cost),
        route_cost_bps=_parse_cost_map(args.route_cost_bps),
        include_filtered=True,
    )
    near = select_near_opportunities(rows, min_edge=args.near_min_edge, top=args.top)
    if args.save:
        records = append_near_opportunities(near, args.near_output, source="near-opportunities")
        print(f"Saved {len(records)} near opportunities to {args.near_output}")
    payload = [_serializable(asdict(item)) for item in near]
    _write_or_print(payload, args.output)


def _depth_sweep(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_kalshi(args.limit, args.query)
    polymarket_markets = _fetch_polymarket(args.limit, args.query)
    rows = sweep_depth(
        limitless_markets,
        polymarket_markets,
        sizes=_parse_sizes(args.sizes),
        min_net_edge=args.min_net_edge,
        safety_buffer=args.safety_buffer,
        min_match_score=args.min_match_score,
        fee_bps=args.fee_bps,
        route_fixed_costs=_parse_cost_map(args.route_fixed_cost),
        route_cost_bps=_parse_cost_map(args.route_cost_bps),
    )
    payload = [_serializable(asdict(item)) for item in rows]
    _write_or_print(payload, args.output)


def _depth_max(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_kalshi(args.limit, args.query)
    polymarket_markets = _fetch_polymarket(args.limit, args.query)
    result = find_max_depth_size(
        query=args.query,
        limitless_markets=limitless_markets,
        polymarket_markets=polymarket_markets,
        min_size=args.min_size,
        max_size=args.max_size,
        step_multiplier=args.step_multiplier,
        min_net_edge=args.min_net_edge,
        safety_buffer=args.safety_buffer,
        min_match_score=args.min_match_score,
        fee_bps=args.fee_bps,
        route_fixed_costs=_parse_cost_map(args.route_fixed_cost),
        route_cost_bps=_parse_cost_map(args.route_cost_bps),
    )
    _write_or_print([_serializable(asdict(result))], args.output)


def _fees(args: argparse.Namespace) -> None:
    markets = _fetch_kalshi(args.limit, args.query) + _fetch_polymarket(args.limit, args.query)
    prices = _parse_sizes(args.prices)
    rows = []
    for market in markets:
        fee_samples = []
        for price in prices:
            fee, notes = estimate_market_taker_fee_per_share(market, price)
            fee_samples.append({"price": price, "fee_per_share": fee, "notes": notes})
        rows.append(
            {
                "source": market.source,
                "market_id": market.market_id,
                "title": market.title,
                "url": market.url,
                "fee_samples": fee_samples,
            }
        )
    _write_or_print(rows, args.output)


def _monitor(args: argparse.Namespace) -> None:
    _validate_monitor_scope(args)
    scope_label = _monitor_scope_label(args)
    previous_keys = _load_monitor_keys(args.output)
    iteration = 0
    try:
        while True:
            iteration += 1
            try:
                limitless_markets, polymarket_markets = _fetch_monitor_universe(args)
                snapshot, previous_keys = monitor_once(
                    query=scope_label,
                    limitless_markets=limitless_markets,
                    polymarket_markets=polymarket_markets,
                    previous_keys=previous_keys,
                    size=args.size,
                    min_net_edge=args.min_net_edge,
                    safety_buffer=args.safety_buffer,
                    min_match_score=args.min_match_score,
                    fee_bps=args.fee_bps,
                    min_profit=args.min_profit,
                    route_fixed_costs=_parse_cost_map(args.route_fixed_cost),
                    route_cost_bps=_parse_cost_map(args.route_cost_bps),
                    max_depth_pairs=args.max_depth_pairs,
                )
                payload = _serializable(asdict(snapshot))
                _append_jsonl(payload, args.output)
                if args.print_snapshots:
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                else:
                    print(
                        f"{snapshot.detected_at.isoformat()} "
                        f"active={snapshot.opportunity_count} new={snapshot.new_count} gone={snapshot.gone_count}"
                    )
                _send_monitor_alert_if_needed(snapshot, args)
                _handle_suspicious_candidates(snapshot, args)
            except Exception as exc:
                if args.stop_on_error:
                    raise
                payload = _monitor_error_payload(scope_label, exc)
                _append_jsonl(payload, args.output)
                print(f"{payload['detected_at']} error={payload['error']}")

            if args.iterations and iteration >= args.iterations:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Monitor stopped.")


def _research_monitor(args: argparse.Namespace) -> None:
    if not _expanded_queries(args) and not args.category:
        args.all_markets = True
    scope_label = _monitor_scope_label(args)
    iteration = 0
    try:
        while True:
            iteration += 1
            try:
                limitless_markets, polymarket_markets = _fetch_monitor_universe(args)
                snapshot = build_research_snapshot(
                    scope=scope_label,
                    limitless_markets=limitless_markets,
                    polymarket_markets=polymarket_markets,
                    size=args.size,
                    min_net_edge=args.min_net_edge,
                    min_profit=args.min_profit,
                    safety_buffer=args.safety_buffer,
                    fee_bps=args.fee_bps,
                    route_fixed_costs=_parse_cost_map(args.route_fixed_cost),
                    route_cost_bps=_parse_cost_map(args.route_cost_bps),
                    min_match_score=args.min_match_score,
                    near_min_edge=args.near_min_edge,
                    top=args.top,
                    max_depth_pairs=args.max_depth_pairs,
                    near_output=args.near_output,
                    save_near=True,
                )
                _append_jsonl(snapshot, args.output)
                if args.print_snapshots:
                    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
                else:
                    matching = snapshot.get("matching", {})
                    print(
                        f"{snapshot['detected_at']} research "
                        f"markets={snapshot['source_counts']} "
                        f"compatible={matching.get('structurally_compatible_pairs', 0)} "
                        f"depth_pairs={snapshot['depth_pairs_scanned']} "
                        f"candidates={snapshot['candidate_count']} "
                        f"near={snapshot['near_count']}"
                    )
            except Exception as exc:
                if args.stop_on_error:
                    raise
                payload = _monitor_error_payload(scope_label, exc)
                payload["type"] = "research_error"
                _append_jsonl(payload, args.output)
                print(f"{payload['detected_at']} research_error={payload['error']}")

            if args.iterations and iteration >= args.iterations:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Research monitor stopped.")


def _monitor_report(args: argparse.Namespace) -> None:
    payload = summarize_monitor_history(args.input, top=args.top)
    _write_or_print([payload], args.output)


def _review_report(args: argparse.Namespace) -> None:
    payload = summarize_review_quality(args.reviews, args.labels, limit=args.limit, examples=args.examples)
    _write_or_print([payload], args.output)


def _capital_plan(args: argparse.Namespace) -> None:
    payload = plan_capital(
        latest_opportunities(args.input),
        parse_balances(args.cash),
        parse_inventory(args.inventory),
        assume_sell_inventory=not args.require_sell_inventory,
        max_allocations=args.max_allocations,
    )
    _write_or_print([payload], args.output)


def _portfolio_init(args: argparse.Namespace) -> None:
    portfolio = initialize_portfolio(args.portfolio, parse_balances(args.cash), overwrite=args.overwrite)
    _write_or_print([portfolio_summary(portfolio)], args.output)


def _portfolio_status(args: argparse.Namespace) -> None:
    _write_or_print([portfolio_summary(load_portfolio(args.portfolio))], args.output)


def _paper_enter(args: argparse.Namespace) -> None:
    payload = paper_enter_from_monitor(
        args.input,
        args.portfolio,
        max_allocations=args.max_allocations,
        require_sell_inventory=args.require_sell_inventory,
    )
    _write_or_print([payload], args.output)


def _paper_close(args: argparse.Namespace) -> None:
    payload = paper_mark_close(args.portfolio, args.key, realized_pnl=args.realized_pnl)
    _write_or_print([payload], args.output)


def _paper_sync(args: argparse.Namespace) -> None:
    payload = paper_sync_from_monitor(args.input, args.portfolio)
    _write_or_print([payload], args.output)


def _paper_loop(args: argparse.Namespace) -> None:
    run_paper_loop(
        args.input,
        args.portfolio,
        interval=args.interval,
        enter=args.enter,
        max_allocations=args.max_allocations,
        require_sell_inventory=args.require_sell_inventory,
    )


def _telegram_test(args: argparse.Namespace) -> None:
    if not args.bot_token or not args.chat_id:
        raise ValueError("--bot-token/--chat-id or TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are required.")
    _post_json(_telegram_send_message_url(args.bot_token), build_telegram_payload(args.chat_id, args.message))
    print("Telegram test message sent.")


def _telegram_bot(args: argparse.Namespace) -> None:
    if not args.bot_token:
        raise ValueError("--bot-token or TELEGRAM_BOT_TOKEN is required.")
    run_telegram_bot(args.bot_token, args.input, allowed_chat_id=args.chat_id, poll_interval=args.poll_interval)


def _dashboard(args: argparse.Namespace) -> None:
    serve_dashboard(args.host, args.port, default_input=args.input)


def _monitor_error_payload(query: str, exc: Exception) -> dict:
    return {
        "type": "error",
        "query": query,
        "detected_at": datetime.now(tz=timezone.utc).isoformat(),
        "error": f"{exc.__class__.__name__}: {exc}",
    }


def _monitor_scope_label(args: argparse.Namespace) -> str:
    parts = []
    queries = _expanded_queries(args)
    if queries:
        parts.append("query=" + ",".join(queries))
    if args.category:
        parts.append("category=" + ",".join(args.category))
    if args.all_markets:
        parts.append("all-markets")
    if args.max_close_hours is not None:
        parts.append(f"max-close-hours={args.max_close_hours:g}")
    return ";".join(parts) or "all-markets"


def _validate_monitor_scope(args: argparse.Namespace) -> None:
    if not _expanded_queries(args) and not args.category and not args.all_markets:
        raise ValueError("monitor requires at least one --query, --category, or --all-markets.")


def _write_or_print(payload: list[dict], output: Path | None) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{text}\n", encoding="utf-8")
        print(f"Wrote {len(payload)} rows to {output}")
        return
    print(text)


def _append_jsonl(payload: object, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def _load_monitor_keys(output: Path) -> set[str]:
    if not output.exists():
        return set()
    last_line = ""
    with output.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last_line = line
    if not last_line:
        return set()
    try:
        payload = json.loads(last_line)
    except json.JSONDecodeError:
        return set()
    keys = payload.get("active_keys", [])
    if not isinstance(keys, list):
        return set()
    return {str(item) for item in keys}


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _send_monitor_alert_if_needed(snapshot: object, args: argparse.Namespace) -> None:
    alert_text = format_new_opportunity_alert(snapshot)
    if alert_text is None:
        return
    if args.alert_new:
        print(alert_text)
    if args.webhook_url:
        payload = build_webhook_payload(alert_text, args.webhook_format)
        _post_json(args.webhook_url, payload)
    if args.telegram_bot_token or args.telegram_chat_id:
        if not args.telegram_bot_token or not args.telegram_chat_id:
            raise ValueError("Both --telegram-bot-token and --telegram-chat-id are required for Telegram alerts.")
        payload = build_telegram_payload(args.telegram_chat_id, alert_text)
        _post_json(_telegram_send_message_url(args.telegram_bot_token), payload)


def _handle_suspicious_candidates(snapshot: object, args: argparse.Namespace) -> None:
    if not (args.save_suspicious or args.alert_suspicious):
        return
    new_keys = set(getattr(snapshot, "new_keys", []) or [])
    suspicious = []
    for candidate in getattr(snapshot, "opportunities", []) or []:
        if new_keys and _opportunity_key(candidate) not in new_keys:
            continue
        risk = assess_candidate_risk(candidate)
        if int(risk["risk_score"]) >= args.suspicious_min_risk or risk["manual_review"]:
            suspicious.append(candidate)
    if not suspicious:
        return

    records = append_review_candidates(suspicious, args.review_output, source="monitor") if args.save_suspicious or args.alert_suspicious else []
    print(f"manual_review={len(records)} saved_to={args.review_output}")

    if not args.alert_suspicious:
        return
    if not args.telegram_bot_token or not args.telegram_chat_id:
        raise ValueError("Both --telegram-bot-token and --telegram-chat-id are required for suspicious Telegram alerts.")
    for record in records[:5]:
        payload = build_telegram_payload(
            args.telegram_chat_id,
            _format_suspicious_review_alert(record),
            reply_markup=_review_inline_keyboard(str(record["review_id"])),
        )
        _post_json(_telegram_send_message_url(args.telegram_bot_token), payload)


def _format_suspicious_review_alert(record: dict[str, object]) -> str:
    candidate = record.get("candidate", {}) if isinstance(record.get("candidate"), dict) else {}
    risk = record.get("risk", {}) if isinstance(record.get("risk"), dict) else {}
    net_edge = _sort_number(candidate.get("net_edge"))
    size = _sort_number(candidate.get("executable_size"))
    profit = net_edge * size
    reasons = ", ".join(_translate_risk_reason(str(item)) for item in risk.get("reasons", []) if item) or "-"
    return "\n".join(
        [
            f"Нужна ручная проверка #{record.get('review_id')}",
            f"Риск: {risk.get('risk_level')} / score={risk.get('risk_score')}",
            f"Маршрут: {candidate.get('outcome')} {candidate.get('buy_source')} -> {candidate.get('sell_source')}",
            f"Net edge: {net_edge:.2%}, размер: {size:g}, оценка прибыли: ${profit:.2f}",
            f"Купить: {candidate.get('buy_title')}",
            f"Продать: {candidate.get('sell_title')}",
            f"Причины: {reasons}",
        ]
    )


def _review_inline_keyboard(review_id: str) -> dict[str, object]:
    return {
        "inline_keyboard": [
            [
                {"text": "То же событие", "callback_data": f"review:same_event:{review_id}"},
                {"text": "Не то", "callback_data": f"review:different_event:{review_id}"},
                {"text": "Сомнительно", "callback_data": f"review:unsure:{review_id}"},
            ]
        ]
    }


def _translate_risk_reason(value: str) -> str:
    return {
        "hard_structural_warning": "жесткое структурное предупреждение",
        "outcome_subject_differs": "исходы относятся к разным командам",
        "price_source_differs": "разный источник цены",
        "price_pair_differs": "разная ценовая пара",
        "low_match_score": "слабое совпадение текста",
        "medium_match_score": "среднее совпадение текста",
        "sports_competition_terms_uncertain": "спортивные условия отличаются",
        "high_net_edge": "высокая доходность",
        "very_high_net_edge": "очень высокая доходность",
        "extreme_net_edge": "аномальная доходность",
        "large_top_depth_gap": "большая разница top/depth",
        "fee_estimate_missing": "нет оценки комиссий",
        "fee_model_uncertain": "комиссии оценены неуверенно",
        "limitless_fee_curve_unknown": "неизвестная кривая комиссии Limitless",
        "kalshi_fee_model_not_implemented_use_manual_fee_bps": "Kalshi комиссия через ручной запас",
        "kalshi_fee_model_uncertain": "Kalshi комиссия оценена неуверенно",
        "low_manual_fee_buffer": "низкий ручной запас комиссии",
        "filtered_candidate": "кандидат отфильтрован",
    }.get(value, value)


def _telegram_send_message_url(bot_token: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/sendMessage"


def _post_json(url: str, payload: dict[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=10) as response:
        response.read()


def _market_dict(market: object, include_raw: bool) -> dict:
    payload = asdict(market)
    if not include_raw:
        payload.pop("raw", None)
    return payload


def _filter_markets(markets: list, min_liquidity: float) -> list:
    if min_liquidity <= 0:
        return markets
    return [market for market in markets if (market.liquidity or 0.0) >= min_liquidity]


def _fetch_monitor_universe(args: argparse.Namespace) -> tuple[list, list]:
    queries = _expanded_queries(args)
    kalshi_seed = kalshi.fetch_markets(limit=args.limit) if args.all_markets or not queries else []
    polymarket_seed = polymarket.fetch_markets_expanded(limit=args.limit) if args.all_markets or not queries else []
    kalshi_query = [
        market
        for query in queries
        for market in _fetch_kalshi(args.limit, query)
    ]
    polymarket_query = [
        market
        for query in queries
        for market in _fetch_polymarket(args.limit, query)
    ]
    kalshi_markets = _dedupe_markets([*kalshi_seed, *kalshi_query])
    polymarket_markets = _dedupe_markets([*polymarket_seed, *polymarket_query])

    if args.category:
        kalshi_markets = _filter_by_any_category(kalshi_markets, args.category)
        polymarket_markets = _filter_by_any_category(polymarket_markets, args.category)

    kalshi_markets = _filter_by_close_window(kalshi_markets, args.min_close_minutes, args.max_close_hours)
    polymarket_markets = _filter_by_close_window(polymarket_markets, args.min_close_minutes, args.max_close_hours)
    return kalshi_markets, polymarket_markets


def _expanded_queries(args: argparse.Namespace) -> list[str]:
    queries = list(getattr(args, "query", []) or [])
    for preset in getattr(args, "preset", []) or []:
        queries.extend(SEARCH_PRESETS.get(str(preset), []))
    seen = set()
    rows = []
    for query in queries:
        normalized = str(query).strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        rows.append(normalized)
    return rows


def _dedupe_markets(markets: object) -> list:
    seen = set()
    rows = []
    for market in markets:
        key = (market.source, market.market_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(market)
    return rows


def _filter_by_any_category(markets: list, categories: list[str]) -> list:
    category_tokens = [_query_tokens(category) for category in categories if _query_tokens(category)]
    if not category_tokens:
        return markets
    return [
        market
        for market in markets
        if any(tokens <= _query_tokens(_match_text(market)) for tokens in category_tokens)
    ]


def _filter_by_close_window(markets: list, min_close_minutes: float | None, max_close_hours: float | None) -> list:
    if min_close_minutes is None and max_close_hours is None:
        return markets
    now = datetime.now(tz=timezone.utc)
    rows = []
    for market in markets:
        close_at = _parse_datetime(getattr(market, "close_time", None))
        if close_at is None:
            continue
        minutes_to_close = (close_at - now).total_seconds() / 60.0
        if minutes_to_close < 0:
            continue
        if min_close_minutes is not None and minutes_to_close < min_close_minutes:
            continue
        if max_close_hours is not None and minutes_to_close > max_close_hours * 60.0:
            continue
        rows.append(market)
    return rows


def _fetch_limitless(limit: int, query: str) -> list:
    return limitless.search_markets(query, limit=limit) if query else limitless.fetch_markets(limit=limit)


def _fetch_kalshi(limit: int, query: str) -> list:
    return kalshi.search_markets(query, limit=limit) if query else kalshi.fetch_markets(limit=limit)


def _fetch_polymarket(limit: int, query: str) -> list:
    return polymarket.search_markets(query, limit=limit) if query else polymarket.fetch_markets_expanded(limit=limit)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _filter_by_query(markets: list, query: str) -> list:
    query_tokens = _query_tokens(query)
    if not query_tokens:
        return markets
    return [market for market in markets if query_tokens <= _query_tokens(_match_text(market))]


def _has_hard_warnings(warnings: list[str]) -> bool:
    return bool(
        {
            "condition_kind_differs",
            "asset_differs",
            "direction_differs",
            "threshold_differs",
            "interval_differs",
            "deadline_differs",
            "outcome_subject_differs",
        }
        & set(warnings)
    )


def _near_miss_sort_key(row: object) -> tuple[float, float, float, float]:
    return (
        _sort_number(getattr(row, "net_edge", None)),
        _sort_number(getattr(row, "depth_edge", None)),
        _sort_number(getattr(row, "top_of_book_edge", None)),
        _sort_number(getattr(row, "match_score", None)),
    )


def _sort_number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -999.0


def _match_text(market: object) -> str:
    raw = getattr(market, "raw", {}) or {}
    pieces = [
        getattr(market, "title", ""),
        raw.get("description", ""),
        raw.get("slug", ""),
        " ".join(str(item) for item in raw.get("categories", []) if item),
        " ".join(str(item) for item in raw.get("tags", []) if item),
        str(raw.get("groupItemTitle") or ""),
    ]
    return _strip_html(" ".join(pieces))


def _candidate_market(market: object) -> dict:
    return {
        "source": market.source,
        "market_id": market.market_id,
        "title": market.title,
        "close_time": market.close_time,
        "volume": market.volume,
        "liquidity": market.liquidity,
        "top": asdict(market.top),
        "url": market.url,
    }


def _query_tokens(value: str) -> set[str]:
    aliases = {"bitcoin": "btc", "ethereum": "eth"}
    return {aliases.get(token, token) for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2}


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _parse_sizes(value: str) -> list[float]:
    sizes = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        sizes.append(float(item))
    return sizes


def _parse_cost_map(value: str) -> dict[str, float]:
    costs: dict[str, float] = {}
    if not value:
        return costs
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        key, amount = item.rsplit("=", 1)
        costs[key.strip()] = float(amount)
    return costs


def _serializable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serializable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


if __name__ == "__main__":
    main()
