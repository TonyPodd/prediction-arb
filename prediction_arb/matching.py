from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from prediction_arb.models import Market


@dataclass(frozen=True)
class Condition:
    kind: str
    asset: str | None
    direction: str | None
    threshold: float | None
    interval_minutes: int | None
    deadline: str | None


@dataclass(frozen=True)
class MatchDetails:
    score: float
    shared_tokens: list[str]
    left_tokens: list[str]
    right_tokens: list[str]
    warnings: list[str]
    left_condition_kind: str
    right_condition_kind: str
    left_condition: Condition | None = None
    right_condition: Condition | None = None


STOP_WORDS = {
    "a",
    "an",
    "and",
    "at",
    "be",
    "before",
    "by",
    "for",
    "from",
    "greater",
    "higher",
    "in",
    "is",
    "it",
    "market",
    "not",
    "of",
    "on",
    "or",
    "otherwise",
    "price",
    "resolve",
    "resolution",
    "source",
    "than",
    "this",
    "the",
    "to",
    "used",
    "will",
    "with",
}

ALIASES = {
    "bitcoin": "btc",
    "ethereum": "eth",
}


def match_score(left: str, right: str) -> float:
    return match_details(left, right).score


def market_match_details(left: Market, right: Market) -> MatchDetails:
    left_condition = condition_from_market(left)
    right_condition = condition_from_market(right)
    details = match_details(left.title, right.title)
    warnings = list(details.warnings)
    warnings.extend(_condition_warnings(left_condition, right_condition))
    return MatchDetails(
        score=details.score,
        shared_tokens=details.shared_tokens,
        left_tokens=details.left_tokens,
        right_tokens=details.right_tokens,
        warnings=sorted(set(warnings)),
        left_condition_kind=left_condition.kind,
        right_condition_kind=right_condition.kind,
        left_condition=left_condition,
        right_condition=right_condition,
    )


def condition_from_market(market: Market) -> Condition:
    raw = market.raw or {}
    text = " ".join(
        str(item)
        for item in [
            market.title,
            raw.get("description", ""),
            raw.get("slug", ""),
            raw.get("groupItemTitle", ""),
        ]
        if item
    )
    kind = _condition_kind(text)
    return Condition(
        kind=kind,
        asset=_asset(text),
        direction=_direction(text, kind),
        threshold=_threshold(text),
        interval_minutes=_interval_minutes(text),
        deadline=_semantic_deadline(text) or _deadline(market.close_time),
    )


def match_details(left: str, right: str) -> MatchDetails:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return MatchDetails(
            score=0.0,
            shared_tokens=[],
            left_tokens=sorted(left_tokens),
            right_tokens=sorted(right_tokens),
            warnings=["missing_tokens"],
            left_condition_kind=_condition_kind(left),
            right_condition_kind=_condition_kind(right),
        )

    overlap = left_tokens & right_tokens
    union = left_tokens | right_tokens
    score = len(overlap) / len(union)
    left_condition_kind = _condition_kind(left)
    right_condition_kind = _condition_kind(right)
    warnings = _warnings(left_tokens, right_tokens, overlap)
    if left_condition_kind != "unknown" and right_condition_kind != "unknown" and left_condition_kind != right_condition_kind:
        warnings.append("condition_kind_differs")
    return MatchDetails(
        score=score,
        shared_tokens=sorted(overlap),
        left_tokens=sorted(left_tokens),
        right_tokens=sorted(right_tokens),
        warnings=warnings,
        left_condition_kind=left_condition_kind,
        right_condition_kind=right_condition_kind,
    )


def _tokens(value: str) -> set[str]:
    return {
        ALIASES.get(token, token)
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in STOP_WORDS
    }


def _warnings(left_tokens: set[str], right_tokens: set[str], overlap: set[str]) -> list[str]:
    warnings = []
    if len(overlap) < 2:
        warnings.append("low_shared_token_count")
    if _has_any(left_tokens, {"up", "down"}) != _has_any(right_tokens, {"up", "down"}):
        warnings.append("directional_terms_only_on_one_side")
    if _has_any(left_tokens, {"cup", "champion", "winner"}) != _has_any(right_tokens, {"cup", "champion", "winner"}):
        warnings.append("competition_terms_only_on_one_side")
    if _date_tokens(left_tokens) != _date_tokens(right_tokens):
        warnings.append("date_tokens_differ")
    return warnings


def _has_any(tokens: set[str], needles: set[str]) -> bool:
    return bool(tokens & needles)


def _date_tokens(tokens: set[str]) -> set[str]:
    return {token for token in tokens if token.isdigit() and len(token) in {4, 8}}


def _condition_kind(value: str) -> str:
    normalized = value.lower()
    if "up or down" in normalized:
        return "directional_up_down"
    if re.search(r"\b(before|by|by end of)\b", normalized):
        return "deadline_yes_no"
    if re.search(r"\b(above|below|over|under)\s+\$?[0-9]", normalized):
        return "threshold"
    if "win the" in normalized and ("world cup" in normalized or "championship" in normalized):
        return "outright_winner"
    if re.search(r"\bwin on \d{4}-\d{2}-\d{2}\b", normalized):
        return "dated_match_winner"
    if re.search(r"\bnext\b", normalized):
        return "next_holder"
    return "unknown"


def _condition_warnings(left: Condition, right: Condition) -> list[str]:
    warnings = []
    if left.kind != "unknown" and right.kind != "unknown" and left.kind != right.kind:
        warnings.append("condition_kind_differs")
    if left.asset and right.asset and left.asset != right.asset:
        warnings.append("asset_differs")
    if left.direction and right.direction and left.direction != right.direction:
        warnings.append("direction_differs")
    if left.threshold is not None and right.threshold is not None and abs(left.threshold - right.threshold) > 0.01:
        warnings.append("threshold_differs")
    if left.interval_minutes is not None and right.interval_minutes is not None and left.interval_minutes != right.interval_minutes:
        warnings.append("interval_differs")
    if left.deadline and right.deadline and left.deadline != right.deadline:
        warnings.append("deadline_differs")
    return warnings


def _asset(value: str) -> str | None:
    normalized = value.lower()
    patterns = {
        "btc": r"\b(btc|bitcoin)\b",
        "eth": r"\b(eth|ethereum)\b",
        "sol": r"\b(sol|solana)\b",
        "xrp": r"\bxrp\b",
        "bnb": r"\bbnb\b",
        "doge": r"\b(doge|dogecoin)\b",
        "hype": r"\bhype\b",
    }
    for asset, pattern in patterns.items():
        if re.search(pattern, normalized):
            return asset
    return None


def _direction(value: str, kind: str) -> str | None:
    normalized = value.lower()
    if kind == "directional_up_down":
        return "up_or_down"
    if kind == "deadline_yes_no":
        return "yes_by_deadline"
    if re.search(r"\b(above|over|greater than|higher than)\b", normalized):
        return "above"
    if re.search(r"\b(below|under|less than|lower than)\b", normalized):
        return "below"
    return None


def _threshold(value: str) -> float | None:
    match = re.search(r"(?:above|below|over|under|greater than|higher than|less than|lower than)\s+\$?([0-9][0-9,]*(?:\.[0-9]+)?)([kmb])?", value.lower())
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = match.group(2)
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    elif suffix == "b":
        number *= 1_000_000_000
    return number


def _interval_minutes(value: str) -> int | None:
    normalized = value.lower()
    patterns = [
        (r"\b(\d+)\s*(?:min|mins|minute|minutes)\b", 1),
        (r"\b(\d+)\s*(?:h|hr|hrs|hour|hours)\b", 60),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1)) * multiplier
    if re.search(r"\bhourly\b", normalized):
        return 60
    if re.search(r"\bdaily\b", normalized):
        return 24 * 60
    if re.search(r"\bweekly\b", normalized):
        return 7 * 24 * 60
    return None


def _semantic_deadline(value: str) -> str | None:
    normalized = value.lower()
    match = re.search(r"\bby end of (\d{4})\b", normalized)
    if match:
        return f"{match.group(1)}-end"
    match = re.search(r"\bbefore (\d{4})\b", normalized)
    if match:
        return f"{int(match.group(1)) - 1}-end"
    match = re.search(r"\bby ([a-z]+) (\d{1,2}),? (\d{4})\b", normalized)
    if match:
        month = _month_number(match.group(1))
        if month:
            return f"{match.group(3)}-{month:02d}-{int(match.group(2)):02d}"
    match = re.search(r"\bon (\d{4})-(\d{2})-(\d{2})\b", normalized)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


def _deadline(value: str | None) -> str | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return value


def _month_number(value: str) -> int | None:
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    return months.get(value)
