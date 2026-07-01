# Prediction Market Arb Scanner

Read-only MVP for scanning Limitless and Polymarket markets and looking for possible cross-venue pricing gaps.

This project does **not** place trades. It only fetches public market data, normalizes prices, and reports potential opportunities that still need manual verification.

## Run

```bash
python -m prediction_arb.cli scan --limit 50
```

Scan only a topic:

```bash
python -m prediction_arb.cli scan --query btc --limit 100
```

Optional JSON output:

```bash
python -m prediction_arb.cli scan --limit 100 --output data/opportunities.json
```

Filter out very thin markets:

```bash
python -m prediction_arb.cli scan --limit 200 --min-liquidity 1000 --min-edge 0.02
```

Dump normalized markets:

```bash
python -m prediction_arb.cli markets --source limitless --limit 20
python -m prediction_arb.cli markets --source polymarket --limit 20
```

Find candidate matches for a topic before scanning for edge:

```bash
python -m prediction_arb.cli candidates --query btc --limit 100
python -m prediction_arb.cli candidates --query "world cup" --limit 100
```

Summarize match quality across topics:

```bash
python -m prediction_arb.cli diagnose --query taiwan --query btc --query "bitcoin reserve" --limit 20
```

Scan executable depth for a target size:

```bash
python -m prediction_arb.cli depth-scan --query taiwan --limit 20 --size 100
python -m prediction_arb.cli depth-scan --query taiwan --limit 20 --size 100 --fee-bps 10 --include-filtered
```

Sweep multiple sizes to estimate where the edge starts breaking:

```bash
python -m prediction_arb.cli depth-sweep --query taiwan --limit 20 --sizes 10,50,100,250,500,1000 --fee-bps 10
```

Find the largest passing size on a geometric grid:

```bash
python -m prediction_arb.cli depth-max --query taiwan --limit 20 --min-size 10 --max-size 100000 --step-multiplier 2 --fee-bps 10
```

Inspect fee assumptions for matching markets:

```bash
python -m prediction_arb.cli fees --query taiwan --limit 20 --prices 0.05,0.5,0.95
```

Monitor live depth opportunities and append snapshots to JSONL:

```bash
python -m prediction_arb.cli monitor --query taiwan --limit 20 --size 100 --fee-bps 10 --interval 30 --output data/monitor-taiwan.jsonl
python -m prediction_arb.cli monitor --query taiwan --limit 20 --size 100 --iterations 1 --print-snapshots
python -m prediction_arb.cli monitor --query taiwan --limit 20 --size 100 --alert-new --webhook-format discord --webhook-url "$DISCORD_WEBHOOK_URL"
python -m prediction_arb.cli monitor --query taiwan --limit 20 --size 100 --telegram-bot-token "$TELEGRAM_BOT_TOKEN" --telegram-chat-id "$TELEGRAM_CHAT_ID"
```

Summarize monitor history:

```bash
python -m prediction_arb.cli monitor-report --input data/monitor-taiwan.jsonl --top 10
```

Test Telegram alerts:

```bash
python -m prediction_arb.cli telegram-test --bot-token "$TELEGRAM_BOT_TOKEN" --chat-id "$TELEGRAM_CHAT_ID"
```

Candidate output includes parsed conditions:

```text
kind: directional_up_down | threshold | outright_winner | dated_match_winner | next_holder
asset: btc | eth | sol | ...
direction: above | below | up_or_down
threshold: numeric threshold when available
deadline: UTC minute when available
```

## Notes

- Limitless and Polymarket prices are normalized to probabilities from `0.0` to `1.0`.
- Polymarket prices are usually decimal probabilities already.
- Matching combines text overlap with basic structured condition checks and returns warnings. Similar-looking markets can still have different settlement rules or resolution sources.
- Multi-word query filtering requires all query terms to appear in the market text.
- `scan` rejects structurally mismatched pairs, including different condition types, assets, directions, thresholds, or semantic deadlines.
- `depth-scan` fetches order books and estimates executable average buy/sell prices for a requested share size.
- `depth-sweep` repeats depth scanning across multiple share sizes and reports best net edge/profit per size.
- `depth-max` searches a geometric size grid and returns the largest size that still passes `--min-net-edge`.
- Fee estimates are per-share. Polymarket fees are zero when `feesEnabled=false`; otherwise the scanner uses `rate * price * (1 - price)` from `feeSchedule` or the documented taker fallback. Limitless exposes fee flags but not the full curve in the market payload, so use `--fee-bps` as a conservative manual overlay.
- `monitor` appends one JSON object per scan to JSONL and reports active, new, and gone opportunity keys. If the JSONL file already exists, the next run resumes comparison from the last saved `active_keys`. Use `--alert-new` for compact terminal alerts, `--webhook-url` for JSON webhook alerts, or `--telegram-bot-token` plus `--telegram-chat-id` for Telegram alerts. Temporary scan failures are stored as error snapshots; use `--stop-on-error` for strict debugging.
- `monitor-report` summarizes JSONL history, counts error snapshots, and ranks routes by the best observed net edge.
- `fee_notes` explains which fee assumptions were applied. `--include-filtered` includes rejected candidates with a reason.
- Any reported opportunity should be treated as a candidate for research, not as a trade signal.

## Test

```bash
python -m unittest discover -s tests
python -m compileall prediction_arb tests
```
