"""
main.py
Orchestrates the full agent loop:
  1. Fetch congressional disclosures
  2. For each new disclosure, fetch news + sentiment
  3. Ask Claude whether to trade
  4. Run through risk manager
  5. Place order via Alpaca
  6. Periodically review open positions for stop-losses
"""

import json
import os
import re
import sys
import schedule
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import anthropic

# Ensure the project root is on sys.path when running as python agent/main.py
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

from data.political import get_all_political_trades, summarise_trades
from data.news import get_news_for_ticker, summarise_news, aggregate_sentiment_score
from data.sec import get_sec_filings, summarise_sec_filings
from agent.prompts import SYSTEM_PROMPT, build_analysis_prompt, build_portfolio_review_prompt
from agent.risk_manager import evaluate, check_stop_losses
from agent.broker import (
    get_portfolio_value,
    get_open_positions,
    get_open_tickers,
    portfolio_snapshot_text,
    place_order,
    close_position,
    is_market_open,
)

POLL_INTERVAL  = int(os.getenv("POLL_INTERVAL_MINUTES", 30))
LOGS_DIR       = Path(__file__).parent.parent / "logs"
SEEN_FILE      = LOGS_DIR / "seen_disclosures.json"

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Persistent seen-disclosure state ──────────────────────────────────────────

def _load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return {tuple(k) for k in json.loads(SEEN_FILE.read_text())}
        except Exception:
            return set()
    return set()

def _save_seen() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    SEEN_FILE.write_text(json.dumps([list(k) for k in _seen_disclosure_keys]))

_seen_disclosure_keys: set = _load_seen()

# ── Daily loss circuit breaker ─────────────────────────────────────────────────

_daily_start_value: float | None = None
_daily_start_date:  str = ""

def _within_daily_loss_limit(portfolio_value: float) -> bool:
    """Returns False and halts trading if the daily loss limit has been hit."""
    global _daily_start_value, _daily_start_date

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if _daily_start_date != today:
        _daily_start_value = portfolio_value
        _daily_start_date  = today
        return True

    max_loss = float(os.getenv("MAX_DAILY_LOSS_PCT", 0.03))
    drawdown = (_daily_start_value - portfolio_value) / _daily_start_value  # type: ignore[operator]
    if drawdown >= max_loss:
        log_event({"event": "daily_loss_halt", "drawdown": round(drawdown, 4), "limit": max_loss})
        print(f"[risk] Daily loss limit reached: {drawdown:.1%} drawdown (limit {max_loss:.0%}). Halting new trades.")
        return False
    return True


# ── Logging (daily rotating files) ────────────────────────────────────────────

def _log_file() -> Path:
    return LOGS_DIR / f"trades_{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"

def log_event(event: dict) -> None:
    log_path = _log_file()
    log_path.parent.mkdir(exist_ok=True)
    entry = {"timestamp": datetime.utcnow().isoformat(), **event}
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[log] {entry}")


# ── Claude analysis ───────────────────────────────────────────────────────────

def ask_claude(prompt: str) -> dict | list:
    """Send a prompt to Claude and parse the JSON response. Retries on API errors."""
    response = None
    last_exc: Exception | None = None

    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                wait = 2 ** attempt * 2
                print(f"[claude] API error (attempt {attempt + 1}): {e}. Retrying in {wait}s...")
                time.sleep(wait)

    if response is None:
        print(f"[claude] API failed after 3 attempts: {last_exc}")
        return {"action": "HOLD", "ticker": "", "confidence": 0.0, "reasoning": "API error", "risk_level": "HIGH"}

    raw = response.content[0].text.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        print(f"[claude] Could not parse response: {raw[:200]}")
        return {"action": "HOLD", "ticker": "", "confidence": 0.0, "reasoning": "Parse error", "risk_level": "HIGH"}


# ── Main signal scan ──────────────────────────────────────────────────────────

def scan_new_disclosures() -> None:
    print(f"\n[agent] === Scanning disclosures at {datetime.utcnow().isoformat()} ===")

    if not is_market_open():
        print("[agent] Market is closed — skipping trade scan.")
        return

    try:
        portfolio_value = get_portfolio_value()
    except Exception as e:
        print(f"[agent] Could not reach Alpaca — skipping scan: {e}")
        return

    if not _within_daily_loss_limit(portfolio_value):
        return

    trades       = get_all_political_trades(days_back=7)
    open_tickers = get_open_tickers()

    # Group trades by ticker so we send one prompt per ticker
    by_ticker: dict[str, list[dict]] = {}
    for t in trades:
        key = (t["member"], t["ticker"], t["transaction_date"])
        if key in _seen_disclosure_keys:
            continue
        _seen_disclosure_keys.add(key)
        by_ticker.setdefault(t["ticker"], []).append(t)

    _save_seen()

    if not by_ticker:
        print("[agent] No new disclosures to evaluate.")
        return

    print(f"[agent] {len(by_ticker)} new ticker(s) to evaluate: {', '.join(by_ticker)}")

    for ticker, ticker_trades in by_ticker.items():
        print(f"\n[agent] Evaluating {ticker}...")

        trade_summary  = summarise_trades(ticker_trades)
        articles       = get_news_for_ticker(ticker)
        news_summary   = summarise_news(articles)
        sentiment      = aggregate_sentiment_score(articles)
        sec_filings    = get_sec_filings(ticker)
        sec_summary    = summarise_sec_filings(sec_filings)
        portfolio_text = portfolio_snapshot_text()

        prompt   = build_analysis_prompt(trade_summary, news_summary, sentiment, portfolio_text, sec_summary)
        decision = ask_claude(prompt)

        log_event({"event": "analysis", "ticker": ticker, "decision": decision})

        approved, order, reason = evaluate(decision, portfolio_value, open_tickers)
        print(f"[risk] {reason}")

        if approved and order:
            result = place_order(order)
            if result:
                open_tickers.append(ticker)
                log_event({"event": "order_placed", "order": order, "result": result})


# ── Periodic portfolio review ─────────────────────────────────────────────────

def review_open_positions() -> None:
    print(f"\n[agent] === Portfolio review at {datetime.utcnow().isoformat()} ===")

    try:
        positions = get_open_positions()
    except Exception as e:
        print(f"[agent] Could not reach Alpaca — skipping review: {e}")
        return

    if not positions:
        print("[agent] No open positions.")
        return

    # 1. Hard stop-losses — close immediately without asking Claude
    stop_tickers = check_stop_losses(positions)
    for ticker in stop_tickers:
        result = close_position(ticker)
        log_event({"event": "stop_loss_close", "ticker": ticker, "result": result})

    remaining = [p for p in positions if p["symbol"] not in stop_tickers]
    if not remaining:
        return

    # 2. Ask Claude if any remaining positions should be closed
    prompt    = build_portfolio_review_prompt(remaining)
    decisions = ask_claude(prompt)

    if not isinstance(decisions, list):
        return

    for decision in decisions:
        if decision.get("action", "").upper() != "SELL":
            continue
        ticker     = decision.get("ticker", "").upper()
        confidence = float(decision.get("confidence", 0.0))
        if confidence >= float(os.getenv("MIN_CONFIDENCE", 0.70)):
            result = close_position(ticker)
            log_event({"event": "ai_close", "ticker": ticker, "decision": decision, "result": result})


# ── Scheduler setup ───────────────────────────────────────────────────────────

def run() -> None:
    print(f"[agent] Starting trading agent (poll every {POLL_INTERVAL} min, DRY_RUN={os.getenv('DRY_RUN')})")
    print("[agent] Press Ctrl+C to stop.\n")

    # Run once immediately on startup
    scan_new_disclosures()
    review_open_positions()

    # Schedule recurring runs
    schedule.every(POLL_INTERVAL).minutes.do(scan_new_disclosures)
    schedule.every(60).minutes.do(review_open_positions)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run()
