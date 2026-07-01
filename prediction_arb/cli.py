from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path

from prediction_arb.depth import estimate_market_taker_fee_per_share, find_max_depth_size, scan_depth_candidates, sweep_depth
from prediction_arb.matching import market_match_details
from prediction_arb.scanner import scan_opportunities
from prediction_arb.sources import limitless, polymarket


def main() -> None:
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


def _write_or_print(payload: list[dict], output: Path | None) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{text}\n", encoding="utf-8")
        print(f"Wrote {len(payload)} rows to {output}")
        return
    print(text)


def _market_dict(market: object, include_raw: bool) -> dict:
    payload = asdict(market)
    if not include_raw:
        payload.pop("raw", None)
    return payload


def _filter_markets(markets: list, min_liquidity: float) -> list:
    if min_liquidity <= 0:
        return markets
    return [market for market in markets if (market.liquidity or 0.0) >= min_liquidity]


def _fetch_limitless(limit: int, query: str) -> list:
    return limitless.search_markets(query, limit=limit) if query else limitless.fetch_markets(limit=limit)


def _fetch_polymarket(limit: int, query: str) -> list:
    return polymarket.search_markets(query, limit=limit) if query else polymarket.fetch_markets(limit=limit)


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
