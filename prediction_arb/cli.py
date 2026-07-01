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
from prediction_arb.dashboard import serve_dashboard
from prediction_arb.depth import estimate_market_taker_fee_per_share, find_max_depth_size, scan_depth_candidates, sweep_depth
from prediction_arb.matching import market_match_details
from prediction_arb.monitor import build_telegram_payload, build_webhook_payload, format_new_opportunity_alert, monitor_once
from prediction_arb.paper import paper_enter_from_monitor, paper_mark_close
from prediction_arb.portfolio import initialize_portfolio, load_portfolio, portfolio_summary
from prediction_arb.reporting import latest_opportunities, summarize_monitor_history
from prediction_arb.scanner import scan_opportunities
from prediction_arb.sources import limitless, polymarket
from prediction_arb.telegram_bot import run_telegram_bot


def main() -> None:
    _load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(prog="prediction-arb")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan Limitless and Polymarket for potential gaps.")
    scan_parser.add_argument("--limit", type=int, default=100)
    scan_parser.add_argument("--min-edge", type=float, default=0.02)
    scan_parser.add_argument("--min-match-score", type=float, default=0.25)
    scan_parser.add_argument("--min-liquidity", type=float, default=0.0)
    scan_parser.add_argument("--query")
    scan_parser.add_argument("--output", type=Path)

    markets_parser = subparsers.add_parser("markets", help="Dump normalized markets from one source.")
    markets_parser.add_argument("--source", choices=["limitless", "polymarket"], required=True)
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

    depth_parser = subparsers.add_parser("depth-scan", help="Scan executable edge using orderbook depth.")
    depth_parser.add_argument("--query", required=True)
    depth_parser.add_argument("--limit", type=int, default=50)
    depth_parser.add_argument("--size", type=float, default=100.0)
    depth_parser.add_argument("--min-net-edge", type=float, default=0.005)
    depth_parser.add_argument("--min-profit", type=float, default=0.0)
    depth_parser.add_argument("--safety-buffer", type=float, default=0.002)
    depth_parser.add_argument("--fee-bps", type=float, default=0.0)
    depth_parser.add_argument("--min-match-score", type=float, default=0.25)
    depth_parser.add_argument("--allow-partial", action="store_true")
    depth_parser.add_argument("--include-filtered", action="store_true")
    depth_parser.add_argument("--output", type=Path)

    sweep_parser = subparsers.add_parser("depth-sweep", help="Run depth scan across multiple share sizes.")
    sweep_parser.add_argument("--query", required=True)
    sweep_parser.add_argument("--limit", type=int, default=50)
    sweep_parser.add_argument("--sizes", default="10,50,100,250,500,1000")
    sweep_parser.add_argument("--min-net-edge", type=float, default=0.005)
    sweep_parser.add_argument("--safety-buffer", type=float, default=0.002)
    sweep_parser.add_argument("--fee-bps", type=float, default=0.0)
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
    max_parser.add_argument("--min-match-score", type=float, default=0.25)
    max_parser.add_argument("--output", type=Path)

    fees_parser = subparsers.add_parser("fees", help="Show fee assumptions for matched source markets.")
    fees_parser.add_argument("--query", required=True)
    fees_parser.add_argument("--limit", type=int, default=20)
    fees_parser.add_argument("--prices", default="0.05,0.5,0.95")
    fees_parser.add_argument("--output", type=Path)

    monitor_parser = subparsers.add_parser("monitor", help="Repeatedly scan depth opportunities and append snapshots to JSONL.")
    monitor_parser.add_argument("--query", action="append", default=[])
    monitor_parser.add_argument("--category", action="append", default=[], help="Filter fetched markets by category/tag/title text. Can be repeated.")
    monitor_parser.add_argument("--all-markets", action="store_true", help="Scan fetched source universes without query filtering.")
    monitor_parser.add_argument("--limit", type=int, default=50)
    monitor_parser.add_argument("--size", type=float, default=100.0)
    monitor_parser.add_argument("--interval", type=float, default=30.0)
    monitor_parser.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted.")
    monitor_parser.add_argument("--min-net-edge", type=float, default=0.005)
    monitor_parser.add_argument("--min-profit", type=float, default=0.0)
    monitor_parser.add_argument("--safety-buffer", type=float, default=0.002)
    monitor_parser.add_argument("--fee-bps", type=float, default=0.0)
    monitor_parser.add_argument("--min-match-score", type=float, default=0.25)
    monitor_parser.add_argument("--min-close-minutes", type=float)
    monitor_parser.add_argument("--max-close-hours", type=float)
    monitor_parser.add_argument("--output", type=Path, default=Path("data/monitor.jsonl"))
    monitor_parser.add_argument("--print-snapshots", action="store_true")
    monitor_parser.add_argument("--alert-new", action="store_true", help="Print a compact alert when new opportunities appear.")
    monitor_parser.add_argument("--webhook-url", help="POST new-opportunity alerts to this webhook URL.")
    monitor_parser.add_argument("--webhook-format", choices=["generic", "discord"], default="generic")
    monitor_parser.add_argument("--telegram-bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"), help="Telegram bot token. Defaults to TELEGRAM_BOT_TOKEN.")
    monitor_parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"), help="Telegram chat id. Defaults to TELEGRAM_CHAT_ID.")
    monitor_parser.add_argument("--stop-on-error", action="store_true", help="Exit instead of appending an error snapshot when a scan fails.")

    report_parser = subparsers.add_parser("monitor-report", help="Summarize monitor JSONL history.")
    report_parser.add_argument("--input", type=Path, required=True)
    report_parser.add_argument("--top", type=int, default=10)
    report_parser.add_argument("--output", type=Path)

    capital_parser = subparsers.add_parser("capital-plan", help="Plan capital allocation across latest monitor opportunities.")
    capital_parser.add_argument("--input", type=Path, required=True)
    capital_parser.add_argument("--cash", default="limitless=250,polymarket=250")
    capital_parser.add_argument("--inventory", default="")
    capital_parser.add_argument("--require-sell-inventory", action="store_true")
    capital_parser.add_argument("--max-allocations", type=int, default=10)
    capital_parser.add_argument("--output", type=Path)

    portfolio_init_parser = subparsers.add_parser("portfolio-init", help="Initialize local paper portfolio state.")
    portfolio_init_parser.add_argument("--portfolio", type=Path, default=Path("data/portfolio.json"))
    portfolio_init_parser.add_argument("--cash", default="limitless=250,polymarket=250")
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

    telegram_test_parser = subparsers.add_parser("telegram-test", help="Send a test Telegram message.")
    telegram_test_parser.add_argument("--bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    telegram_test_parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"))
    telegram_test_parser.add_argument("--message", default="prediction-arb Telegram alerts are configured")

    telegram_bot_parser = subparsers.add_parser("telegram-bot", help="Run Telegram command bot for monitor status/report.")
    telegram_bot_parser.add_argument("--bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    telegram_bot_parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"))
    telegram_bot_parser.add_argument("--input", type=Path, default=Path("data/monitor-taiwan.jsonl"))
    telegram_bot_parser.add_argument("--poll-interval", type=float, default=2.0)

    dashboard_parser = subparsers.add_parser("dashboard", help="Serve local monitor dashboard.")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8765)
    dashboard_parser.add_argument("--input", type=Path, default=Path("data/monitor-short-all.jsonl"))

    args = parser.parse_args()
    if args.command == "scan":
        _scan(args)
    elif args.command == "markets":
        _markets(args)
    elif args.command == "candidates":
        _candidates(args)
    elif args.command == "diagnose":
        _diagnose(args)
    elif args.command == "depth-scan":
        _depth_scan(args)
    elif args.command == "depth-sweep":
        _depth_sweep(args)
    elif args.command == "depth-max":
        _depth_max(args)
    elif args.command == "fees":
        _fees(args)
    elif args.command == "monitor":
        _monitor(args)
    elif args.command == "monitor-report":
        _monitor_report(args)
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
    elif args.command == "telegram-test":
        _telegram_test(args)
    elif args.command == "telegram-bot":
        _telegram_bot(args)
    elif args.command == "dashboard":
        _dashboard(args)


def _scan(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_limitless(args.limit, args.query or "")
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
    if args.source == "limitless":
        markets = limitless.fetch_markets(limit=args.limit)
    else:
        markets = polymarket.fetch_markets(limit=args.limit)
    payload = [_serializable(_market_dict(item, include_raw=args.include_raw)) for item in markets]
    _write_or_print(payload, args.output)


def _candidates(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_limitless(args.limit, args.query)
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
        limitless_markets = _fetch_limitless(args.limit, query)
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


def _depth_scan(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_limitless(args.limit, args.query)
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
        include_filtered=args.include_filtered,
    )
    payload = [_serializable(asdict(item)) for item in rows]
    _write_or_print(payload, args.output)


def _depth_sweep(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_limitless(args.limit, args.query)
    polymarket_markets = _fetch_polymarket(args.limit, args.query)
    rows = sweep_depth(
        limitless_markets,
        polymarket_markets,
        sizes=_parse_sizes(args.sizes),
        min_net_edge=args.min_net_edge,
        safety_buffer=args.safety_buffer,
        min_match_score=args.min_match_score,
        fee_bps=args.fee_bps,
    )
    payload = [_serializable(asdict(item)) for item in rows]
    _write_or_print(payload, args.output)


def _depth_max(args: argparse.Namespace) -> None:
    limitless_markets = _fetch_limitless(args.limit, args.query)
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
    )
    _write_or_print([_serializable(asdict(result))], args.output)


def _fees(args: argparse.Namespace) -> None:
    markets = _fetch_limitless(args.limit, args.query) + _fetch_polymarket(args.limit, args.query)
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


def _monitor_report(args: argparse.Namespace) -> None:
    payload = summarize_monitor_history(args.input, top=args.top)
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
    if args.query:
        parts.append("query=" + ",".join(args.query))
    if args.category:
        parts.append("category=" + ",".join(args.category))
    if args.all_markets:
        parts.append("all-markets")
    if args.max_close_hours is not None:
        parts.append(f"max-close-hours={args.max_close_hours:g}")
    return ";".join(parts) or "all-markets"


def _validate_monitor_scope(args: argparse.Namespace) -> None:
    if not args.query and not args.category and not args.all_markets:
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
    if args.query:
        limitless_markets = _dedupe_markets(
            market
            for query in args.query
            for market in _fetch_limitless(args.limit, query)
        )
        polymarket_markets = _dedupe_markets(
            market
            for query in args.query
            for market in _fetch_polymarket(args.limit, query)
        )
    else:
        limitless_markets = limitless.fetch_markets(limit=args.limit)
        polymarket_markets = polymarket.fetch_markets(limit=args.limit)

    if args.category:
        limitless_markets = _filter_by_any_category(limitless_markets, args.category)
        polymarket_markets = _filter_by_any_category(polymarket_markets, args.category)

    limitless_markets = _filter_by_close_window(limitless_markets, args.min_close_minutes, args.max_close_hours)
    polymarket_markets = _filter_by_close_window(polymarket_markets, args.min_close_minutes, args.max_close_hours)
    return limitless_markets, polymarket_markets


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
        if min_close_minutes is not None and minutes_to_close < min_close_minutes:
            continue
        if max_close_hours is not None and minutes_to_close > max_close_hours * 60.0:
            continue
        rows.append(market)
    return rows


def _fetch_limitless(limit: int, query: str) -> list:
    return limitless.search_markets(query, limit=limit) if query else limitless.fetch_markets(limit=limit)


def _fetch_polymarket(limit: int, query: str) -> list:
    return polymarket.search_markets(query, limit=limit) if query else polymarket.fetch_markets(limit=limit)


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
            "deadline_differs",
        }
        & set(warnings)
    )


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
