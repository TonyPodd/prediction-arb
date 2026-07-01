from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TopOfBook:
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None


@dataclass(frozen=True)
class OrderLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    source: str
    token_id: str
    outcome: str
    bids: list[OrderLevel]
    asks: list[OrderLevel]


@dataclass(frozen=True)
class ExecutionQuote:
    side: str
    outcome: str
    requested_size: float
    filled_size: float
    avg_price: float | None
    worst_price: float | None
    notional: float
    complete: bool


@dataclass(frozen=True)
class Market:
    source: str
    market_id: str
    title: str
    url: str | None
    close_time: str | None
    volume: float | None
    liquidity: float | None
    top: TopOfBook
    raw: dict


@dataclass(frozen=True)
class Opportunity:
    side: str
    buy_source: str
    buy_market_id: str
    buy_title: str
    buy_price: float
    sell_source: str
    sell_market_id: str
    sell_title: str
    sell_price: float
    gross_edge: float
    match_score: float
    match_warnings: list[str]
    buy_condition_kind: str
    sell_condition_kind: str
    buy_url: str | None
    sell_url: str | None
    detected_at: datetime


@dataclass(frozen=True)
class DepthOpportunity:
    outcome: str
    buy_source: str
    buy_market_id: str
    buy_title: str
    sell_source: str
    sell_market_id: str
    sell_title: str
    top_of_book_edge: float | None
    depth_edge: float
    net_edge: float
    safety_buffer: float
    fee_estimate: float | None
    rejection_reason: str | None
    executable_size: float
    buy_quote: ExecutionQuote
    sell_quote: ExecutionQuote
    match_score: float
    match_warnings: list[str]
    buy_url: str | None
    sell_url: str | None
    detected_at: datetime


@dataclass(frozen=True)
class DepthCandidate:
    outcome: str
    buy_source: str
    buy_market_id: str
    buy_title: str
    sell_source: str
    sell_market_id: str
    sell_title: str
    top_of_book_edge: float | None
    depth_edge: float | None
    net_edge: float | None
    safety_buffer: float
    fee_estimate: float | None
    fee_notes: list[str]
    rejection_reason: str | None
    executable_size: float
    buy_quote: ExecutionQuote
    sell_quote: ExecutionQuote
    match_score: float
    match_warnings: list[str]
    buy_url: str | None
    sell_url: str | None
    detected_at: datetime


@dataclass(frozen=True)
class DepthSweepRow:
    size: float
    opportunities: list[DepthCandidate]
    best_net_edge: float | None
    best_net_profit: float | None
    best_outcome: str | None
    best_route: str | None


@dataclass(frozen=True)
class DepthMaxResult:
    query: str
    min_size: float
    max_size: float
    step_multiplier: float
    max_passing_size: float | None
    best_at_max_size: DepthCandidate | None
    checked_sizes: list[DepthSweepRow]
