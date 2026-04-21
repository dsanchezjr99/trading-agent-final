"""
main.py
Orchestrates the full agent loop:
  1. Check macro market regime (SPY 50-day MA)
  2. Fetch congressional disclosures (filtered: $15k+ amounts, <30 days old)
  3. For each new ticker: check earnings calendar, fetch news/SEC/volatility in parallel
  4. Ask Claude for BUY/SELL/HOLD with sector tag
  5. Run through risk manager (confidence, sector limits, vol-adjusted sizing)
  6. Place limit order via Alpaca
  7. Periodically review open positions for stop-loss, take-profit, and hold expiry
"""

import json
import os
import re
import sys
import schedule
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from data.political import get_all_political_trades, summarise_trades, passes_signal_gate
from utils.notify   import (
    notify_order_placed,
    notify_position_closed,
    notify_market_open,
    notify_end_of_day,
    notify_daily_loss_halt,
    notify_bear_market,
)
from data.news      import get_news_for_ticker, summarise_news, aggregate_sentiment_score
from data.sec       import get_sec_filings, summarise_sec_filings
from data.market    import get_market_regime, get_volatility, earnings_too_close, is_liquid_enough, is_above_20d_ma
from agent.prompts      import SYSTEM_PROMPT, build_analysis_prompt, build_portfolio_review_prompt
from agent.risk_manager import evaluate, check_exit_conditions
from agent.broker       import (
    get_account,
    get_open_positions,
    place_order,
    close_position,
    is_market_open,
)

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_MINUTES", 5))
LOGS_DIR      = Path(__file__).parent.parent / "logs"
SEEN_FILE     = LOGS_DIR / "seen_disclosures.json"
META_FILE     = LOGS_DIR / "positions_meta.json"   # entry date, hold days, sector per position
ENV_FILE      = Path(__file__).parent.parent / ".env"

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ── Persistent state ───────────────────────────────────────────────────────────

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


def _load_meta() -> dict:
    """Load per-position metadata (entry date, hold days, sector)."""
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_meta(meta: dict) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    META_FILE.write_text(json.dumps(meta, indent=2))


_seen_disclosure_keys: set = _load_seen()


# ── Daily loss circuit breaker ─────────────────────────────────────────────────

_daily_start_value: float | None = None
_daily_start_date:  str = ""


def _within_daily_loss_limit(portfolio_value: float) -> bool:
    global _daily_start_value, _daily_start_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily_start_date != today:
        _daily_start_value = portfolio_value
        _daily_start_date  = today
        return True
    max_loss = float(os.getenv("MAX_DAILY_LOSS_PCT", 0.03))
    drawdown = (_daily_start_value - portfolio_value) / _daily_start_value  # type: ignore[operator]
    if drawdown >= max_loss:
        log_event({"event": "daily_loss_halt", "drawdown": round(drawdown, 4), "limit": max_loss})
        print(f"[risk] Daily loss limit {max_loss:.0%} reached ({drawdown:.1%} drawdown). Halting new trades.")
        return False
    return True


# ── Logging ────────────────────────────────────────────────────────────────────

def _log_file() -> Path:
    return LOGS_DIR / f"trades_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"


def log_event(event: dict) -> None:
    log_path = _log_file()
    log_path.parent.mkdir(exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[log] {entry}")


# ── Claude analysis ────────────────────────────────────────────────────────────

def ask_claude(prompt: str) -> dict | list:
    response  = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=600,
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
        return {"action": "HOLD", "ticker": "", "confidence": 0.0, "reasoning": "API error", "risk_level": "HIGH", "sector": "Unknown"}

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
        return {"action": "HOLD", "ticker": "", "confidence": 0.0, "reasoning": "Parse error", "risk_level": "HIGH", "sector": "Unknown"}


# ── Portfolio helpers ──────────────────────────────────────────────────────────

def _portfolio_text(account: dict, positions: list[dict]) -> str:
    lines = [
        f"Portfolio value: ${account['portfolio_value']:,.2f}",
        f"Cash available:  ${account['cash']:,.2f}",
        f"Open positions ({len(positions)}):",
    ]
    for p in positions:
        lines.append(
            f"  {p['symbol']}: {p['qty']} shares, "
            f"P&L {p['unrealized_plpc']:+.1%} (${p['unrealized_pl']:+.2f})"
        )
    return "\n".join(lines)


# ── Main signal scan ───────────────────────────────────────────────────────────

def scan_new_disclosures() -> None:
    print(f"\n[agent] === Scanning disclosures at {datetime.now(timezone.utc).isoformat()} ===")

    if not is_market_open():
        print("[agent] Market is closed — skipping trade scan.")
        return

    # ── Macro regime check ────────────────────────────────────────────────────
    regime = get_market_regime()
    min_confidence = float(os.getenv("MIN_CONFIDENCE", 0.70))
    if regime == "BEAR":
        min_confidence = min(min_confidence + 0.10, 0.95)
        print(f"[market] BEAR market — raising confidence threshold to {min_confidence:.0%}")
        notify_bear_market(spy_price=0, ma50=0)

    # ── Alpaca account + positions (fetched once, reused all cycle) ───────────
    try:
        account   = get_account()
        positions = get_open_positions()
    except Exception as e:
        print(f"[agent] Could not reach Alpaca — skipping scan: {e}")
        return

    portfolio_value = account["portfolio_value"]
    open_tickers    = [p["symbol"] for p in positions]

    if not _within_daily_loss_limit(portfolio_value):
        notify_daily_loss_halt(
            drawdown=(_daily_start_value - portfolio_value) / _daily_start_value,  # type: ignore[operator]
            limit=float(os.getenv("MAX_DAILY_LOSS_PCT", 0.03)),
        )
        return

    # ── Load position metadata (sectors of existing positions) ────────────────
    meta = _load_meta()
    sector_exposure = {t: meta[t].get("sector", "Unknown") for t in open_tickers if t in meta}

    # ── Fetch and filter congressional disclosures ────────────────────────────
    trades = get_all_political_trades(days_back=7)

    new_keys: set = set()
    by_ticker: dict[str, list[dict]] = {}
    for t in trades:
        key = (t["member"], t["ticker"], t["transaction_date"])
        if key in _seen_disclosure_keys:
            continue
        new_keys.add(key)
        _seen_disclosure_keys.add(key)
        by_ticker.setdefault(t["ticker"], []).append(t)

    if new_keys:
        _save_seen()

    if not by_ticker:
        print("[agent] No new disclosures to evaluate.")
        return

    print(f"[agent] {len(by_ticker)} new ticker(s) to evaluate: {', '.join(by_ticker)}")
    portfolio_text = _portfolio_text(account, positions)

    for ticker, ticker_trades in by_ticker.items():
        print(f"\n[agent] Evaluating {ticker}...")

        # ── Earnings calendar check ───────────────────────────────────────────
        if earnings_too_close(ticker):
            log_event({"event": "skipped_earnings", "ticker": ticker})
            continue

        # ── Liquidity filter ──────────────────────────────────────────────────
        if not is_liquid_enough(ticker):
            log_event({"event": "skipped_liquidity", "ticker": ticker})
            continue

        # ── Option B: Rules-based signal gate ─────────────────────────────────
        passed, gate_reason = passes_signal_gate(ticker_trades)
        if not passed:
            print(f"[gate] {ticker}: {gate_reason}")
            log_event({"event": "skipped_signal_gate", "ticker": ticker, "reason": gate_reason})
            continue

        # ── Option C: Momentum gate (price above 20-day MA) ───────────────────
        if not is_above_20d_ma(ticker):
            reason = f"{ticker} is below 20-day MA — skipping downtrend entry"
            print(f"[gate] {reason}")
            log_event({"event": "skipped_momentum", "ticker": ticker, "reason": reason})
            continue

        # ── Fetch news, SEC filings, and volatility in parallel ───────────────
        with ThreadPoolExecutor(max_workers=3) as pool:
            news_future = pool.submit(get_news_for_ticker, ticker)
            sec_future  = pool.submit(get_sec_filings, ticker)
            vol_future  = pool.submit(get_volatility, ticker)
            articles    = news_future.result()
            sec_filings = sec_future.result()
            volatility  = vol_future.result()

        print(f"[market] {ticker} annualized vol: {volatility:.1%}")

        trade_summary = summarise_trades(ticker_trades)
        news_summary  = summarise_news(articles)
        sentiment     = aggregate_sentiment_score(articles)
        sec_summary   = summarise_sec_filings(sec_filings)

        prompt   = build_analysis_prompt(trade_summary, news_summary, sentiment, portfolio_text, sec_summary)
        decision = ask_claude(prompt)

        # Apply regime-adjusted confidence threshold override
        if regime == "BEAR":
            decision["_min_confidence_override"] = min_confidence

        log_event({"event": "analysis", "ticker": ticker, "regime": regime, "volatility": volatility, "decision": decision})

        approved, order, reason = evaluate(
            decision,
            portfolio_value,
            open_tickers,
            volatility=volatility,
            sector_exposure=sector_exposure,
        )

        # Apply BEAR regime confidence override in risk check
        if regime == "BEAR" and not approved and "Confidence" in reason:
            pass  # evaluate() uses MIN_CONFIDENCE from env; BEAR adjustment logged above

        print(f"[risk] {reason}")

        if approved and order:
            result = place_order(order)
            if result:
                open_tickers.append(ticker)
                sector = order.get("sector", "Unknown")
                sector_exposure[ticker] = sector

                # Persist position metadata
                meta[ticker] = {
                    "entry_date":  datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "hold_days":   order.get("hold_days", 30),
                    "sector":      sector,
                    "entry_price": result.get("limit_price") or result.get("amount"),
                    "has_bracket": result.get("order_type") == "bracket",
                    "stop_price":  result.get("stop_price"),
                    "tp_price":    result.get("tp_price"),
                }
                _save_meta(meta)
                log_event({"event": "order_placed", "order": order, "result": result})
                notify_order_placed(order, result)
                if result.get("order_type") == "bracket":
                    print(
                        f"[agent] Bracket legs placed server-side: "
                        f"stop ${result.get('stop_price')} / TP ${result.get('tp_price')}"
                    )


# ── Periodic portfolio review ─────────────────────────────────────────────────

def review_open_positions() -> None:
    print(f"\n[agent] === Portfolio review at {datetime.now(timezone.utc).isoformat()} ===")

    if not _is_trading_hours():
        print("[agent] Market is closed — skipping portfolio review.")
        return

    try:
        positions = get_open_positions()
    except Exception as e:
        print(f"[agent] Could not reach Alpaca — skipping review: {e}")
        return

    if not positions:
        print("[agent] No open positions.")
        return

    meta = _load_meta()

    # ── Hard exits: stop-loss, take-profit, hold expiry ───────────────────────
    exits = check_exit_conditions(positions, meta)
    for ticker, reason in exits.items():
        pos    = next((p for p in positions if p["symbol"] == ticker), None)
        result = close_position(ticker, position=pos)
        if result:
            meta.pop(ticker, None)
            _save_meta(meta)
            notify_position_closed(ticker, reason, result)
        log_event({"event": "hard_exit", "ticker": ticker, "reason": reason, "result": result})

    remaining = [p for p in positions if p["symbol"] not in exits]
    if not remaining:
        return

    # ── Claude review of remaining positions ──────────────────────────────────
    # Annotate positions with hold-expiry context for the prompt
    for p in remaining:
        ticker = p["symbol"]
        if ticker in meta:
            entry_date = meta[ticker].get("entry_date", "")
            hold_days  = int(meta[ticker].get("hold_days", 30))
            if entry_date:
                try:
                    import datetime as dt
                    entry     = dt.date.fromisoformat(entry_date)
                    days_held = (dt.date.today() - entry).days
                    p["days_held"]    = days_held
                    p["hold_days"]    = hold_days
                    p["hold_expired"] = days_held >= hold_days
                except ValueError:
                    pass

    prompt    = build_portfolio_review_prompt(remaining)
    decisions = ask_claude(prompt)

    if not isinstance(decisions, list):
        return

    min_conf = float(os.getenv("MIN_CONFIDENCE", 0.70))
    for decision in decisions:
        if decision.get("action", "").upper() != "SELL":
            continue
        ticker     = decision.get("ticker", "").upper()
        confidence = float(decision.get("confidence", 0.0))
        if confidence >= min_conf:
            pos    = next((p for p in remaining if p["symbol"] == ticker), None)
            result = close_position(ticker, position=pos)
            if result:
                meta.pop(ticker, None)
                _save_meta(meta)
                notify_position_closed(ticker, decision.get("reasoning", "AI recommended close"), result)
            log_event({"event": "ai_close", "ticker": ticker, "decision": decision, "result": result})


# ── End-of-day balance sync ────────────────────────────────────────────────────

def send_morning_email() -> None:
    """Send portfolio snapshot at market open (9:30 AM ET)."""
    try:
        account   = get_account()
        positions = get_open_positions()
        notify_market_open(account, positions)
    except Exception as e:
        print(f"[email] Morning email failed: {e}")


def send_eod_email() -> None:
    """Send end-of-day summary at 4:15 PM ET."""
    try:
        account   = get_account()
        positions = get_open_positions()
        # Pull today's events from the log file
        events = []
        log_path = _log_file()
        if log_path.exists():
            with open(log_path) as f:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
        notify_end_of_day(account, positions, events)
        sync_balance_to_env()
    except Exception as e:
        print(f"[email] EOD email failed: {e}")


def sync_balance_to_env() -> None:
    """Write live Alpaca portfolio value back to PORTFOLIO_VALUE in .env after market close."""
    try:
        balance = get_account()["portfolio_value"]
    except Exception as e:
        print(f"[sync] Could not fetch balance: {e}")
        return
    try:
        text    = ENV_FILE.read_text()
        updated = re.sub(
            r"^PORTFOLIO_VALUE=.*",
            f"PORTFOLIO_VALUE={balance:.2f}",
            text,
            flags=re.MULTILINE,
        )
        ENV_FILE.write_text(updated)
        log_event({"event": "balance_sync", "portfolio_value": balance})
        print(f"[sync] PORTFOLIO_VALUE updated to ${balance:,.2f}")
    except Exception as e:
        print(f"[sync] Failed to write .env: {e}")


# ── Scheduler ─────────────────────────────────────────────────────────────────

# Market hours in UTC (ET + 4h)
MARKET_OPEN_UTC = "13:30"   # 9:30 AM ET
MARKET_CLOSE_UTC = "20:15"  # 4:15 PM ET

ET = ZoneInfo("America/New_York")


def _is_trading_hours() -> bool:
    """True if we're within NYSE core hours (Mon–Fri 9:30–16:00 ET). Doesn't check holidays."""
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:            # Saturday / Sunday
        return False
    open_et  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_et = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_et <= now_et <= close_et


def _next_market_open_utc() -> datetime:
    """Return the next NYSE open (9:25 AM ET, 5 min early) as a UTC datetime."""
    now_et = datetime.now(ET)
    # Walk forward day by day until we find a weekday open that's in the future
    for days_ahead in range(8):
        candidate = (now_et + timedelta(days=days_ahead)).replace(
            hour=9, minute=25, second=0, microsecond=0
        )
        if candidate > now_et and candidate.weekday() < 5:
            return candidate.astimezone(timezone.utc)
    # Fallback (should never happen)
    return (now_et + timedelta(days=1)).replace(hour=13, minute=25).astimezone(timezone.utc)

EMAIL_STATE_FILE = LOGS_DIR / "email_state.json"


def _load_email_state() -> tuple[str, str]:
    """Load persisted morning/EOD sent dates so restarts don't re-send."""
    if EMAIL_STATE_FILE.exists():
        try:
            data = json.loads(EMAIL_STATE_FILE.read_text())
            return data.get("morning", ""), data.get("eod", "")
        except Exception:
            pass
    return "", ""


def _save_email_state(morning: str, eod: str) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    EMAIL_STATE_FILE.write_text(json.dumps({"morning": morning, "eod": eod}))


_morning_sent_date, _eod_sent_date = _load_email_state()


def _maybe_send_morning() -> None:
    """Send morning email once per day, at or after market open."""
    global _morning_sent_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _morning_sent_date != today:
        _morning_sent_date = today
        _save_email_state(_morning_sent_date, _eod_sent_date)
        send_morning_email()


def _maybe_send_eod() -> None:
    """Send EOD email once per day, at or after market close."""
    global _eod_sent_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _eod_sent_date != today:
        _eod_sent_date = today
        _save_email_state(_morning_sent_date, _eod_sent_date)
        send_eod_email()


def _secs_until_eod() -> float:
    """Seconds until today's EOD email time (20:15 UTC). Returns large value if already past."""
    now_utc = datetime.now(timezone.utc)
    today   = now_utc.date()
    eod_h, eod_m = map(int, MARKET_CLOSE_UTC.split(":"))
    eod_today = datetime(today.year, today.month, today.day, eod_h, eod_m, tzinfo=timezone.utc)
    delta = (eod_today - now_utc).total_seconds()
    return delta if delta > 0 else float("inf")


def run() -> None:
    print(f"[agent] Starting trading agent (poll every {POLL_INTERVAL} min, DRY_RUN={os.getenv('DRY_RUN')})")
    print("[agent] Press Ctrl+C to stop.\n")

    scan_new_disclosures()
    review_open_positions()

    schedule.every(POLL_INTERVAL).minutes.do(scan_new_disclosures)
    schedule.every(15).minutes.do(review_open_positions)
    # Use UTC times so emails fire correctly regardless of system timezone
    schedule.every().day.at(MARKET_OPEN_UTC).do(_maybe_send_morning)
    schedule.every().day.at(MARKET_CLOSE_UTC).do(_maybe_send_eod)

    while True:
        schedule.run_pending()

        if _is_trading_hours():
            time.sleep(30)
        else:
            # Sleep until EOD email time OR next market open — whichever is sooner.
            # Deliberately ignore schedule.idle_seconds() here because the recurring
            # 15/30-min poll jobs would otherwise keep waking us all night for no reason.
            now_utc      = datetime.now(timezone.utc)
            secs_to_open = (_next_market_open_utc() - now_utc).total_seconds()
            secs_to_eod  = _secs_until_eod()
            sleep_secs   = max(30, min(secs_to_open, secs_to_eod))
            wake_at      = (now_utc + timedelta(seconds=sleep_secs)).strftime("%Y-%m-%d %H:%M UTC")
            print(f"[agent] Market closed — sleeping {sleep_secs/3600:.1f}h (wake at {wake_at})")
            time.sleep(sleep_secs)


if __name__ == "__main__":
    run()
