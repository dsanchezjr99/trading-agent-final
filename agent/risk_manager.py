"""
risk_manager.py
Validates every AI decision before it reaches the broker.
Enforces position sizing, stop-loss/take-profit, and exposure limits.
"""

import os
from dotenv import load_dotenv
from data.market import vol_size_scalar

load_dotenv()

MAX_POSITION_PCT     = float(os.getenv("MAX_POSITION_PCT",     0.15))
MAX_OPEN_POSITIONS   = int(os.getenv("MAX_OPEN_POSITIONS",     8))
MAX_SECTOR_POSITIONS = int(os.getenv("MAX_SECTOR_POSITIONS",   2))
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT",        0.05))
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT",      0.20))
MIN_CONFIDENCE       = float(os.getenv("MIN_CONFIDENCE",       0.70))
# Hard cap scales with portfolio: no fixed dollar ceiling.
# HARD_CAP_PCT caps any single position as a % of total portfolio value.
# Defaults to MAX_POSITION_PCT so it matches the sizing intent and grows
# automatically as the account grows (was hardcoded at $12,500).
HARD_CAP_PCT         = float(os.getenv("HARD_CAP_PCT", MAX_POSITION_PCT))


def evaluate(
    decision:        dict,
    portfolio_value: float,
    open_positions:  list[str],
    volatility:      float = 0.20,
    sector_exposure: dict  = None,   # {ticker: sector} for currently open positions
) -> tuple[bool, dict | None, str]:
    """
    Validate an AI trade decision through 7 gates, then size the position.

    Returns:
        approved (bool)
        order (dict | None)  — order payload if approved
        reason (str)         — human-readable verdict
    """
    if sector_exposure is None:
        sector_exposure = {}

    action     = decision.get("action", "HOLD").upper()
    ticker     = decision.get("ticker", "").upper()
    confidence = float(decision.get("confidence", 0.0))
    risk_level = decision.get("risk_level", "HIGH").upper()
    sector     = decision.get("sector", "Unknown")

    # ── Gate 1: HOLD passthrough ──────────────────────────────────────────────
    if action == "HOLD":
        return False, None, "AI decided HOLD — no order placed."

    # ── Gate 2: Minimum confidence ────────────────────────────────────────────
    if confidence < MIN_CONFIDENCE:
        return False, None, f"Confidence {confidence:.0%} below threshold {MIN_CONFIDENCE:.0%}."

    # ── Gate 3: Reject HIGH-risk signals ──────────────────────────────────────
    if risk_level == "HIGH":
        return False, None, "Risk level HIGH — skipping to protect capital."

    # ── Gate 4: Max open positions ────────────────────────────────────────────
    if action == "BUY" and len(open_positions) >= MAX_OPEN_POSITIONS:
        return False, None, f"Already at max {MAX_OPEN_POSITIONS} open positions."

    # ── Gate 5: No duplicate positions ───────────────────────────────────────
    if action == "BUY" and ticker in open_positions:
        return False, None, f"Already holding {ticker} — skipping duplicate entry."

    # ── Gate 6: Sector concentration limit ───────────────────────────────────
    if action == "BUY" and sector and sector != "Unknown":
        sector_count = sum(
            1 for s in sector_exposure.values()
            if s.lower() == sector.lower()
        )
        if sector_count >= MAX_SECTOR_POSITIONS:
            return False, None, (
                f"Sector limit reached: already have {sector_count} position(s) in {sector}."
            )

    # ── Gate 7: Must have a valid ticker ─────────────────────────────────────
    if not ticker or len(ticker) > 5:
        return False, None, f"Invalid ticker: '{ticker}'."

    # ── Volatility-adjusted position sizing ───────────────────────────────────
    # Base size from portfolio percentage
    raw_size = portfolio_value * MAX_POSITION_PCT

    # Scale for confidence: 70% conf → ~50% of max, 100% → full max
    confidence_scalar = (confidence - MIN_CONFIDENCE) / (1.0 - MIN_CONFIDENCE)
    conf_adjusted = raw_size * (0.5 + 0.5 * confidence_scalar)

    # Scale down for high-volatility tickers (high-vol → smaller position)
    vol_adjusted  = conf_adjusted * vol_size_scalar(volatility)

    # Apply portfolio-relative hard cap (grows automatically as account grows)
    hard_cap      = portfolio_value * HARD_CAP_PCT
    dollar_amount = min(vol_adjusted, hard_cap)
    dollar_amount = round(dollar_amount, 2)

    if dollar_amount < 1.0:
        return False, None, "Calculated position size too small (< $1)."

    order = {
        "ticker":        ticker,
        "action":        action,
        "dollar_amount": dollar_amount,
        "stop_loss_pct": STOP_LOSS_PCT,
        "take_profit_pct": TAKE_PROFIT_PCT,
        "confidence":    confidence,
        "reasoning":     decision.get("reasoning", ""),
        "hold_days":     decision.get("suggested_hold_days", 30),
        "sector":        sector,
        "volatility":    round(volatility, 4),
    }

    return True, order, (
        f"Approved: {action} ${dollar_amount:.2f} of {ticker} "
        f"(confidence {confidence:.0%}, vol {volatility:.0%}, sector {sector}, risk {risk_level}, "
        f"hard cap ${hard_cap:,.0f})"
    )


def check_exit_conditions(positions: list[dict], meta: dict) -> dict[str, str]:
    """
    Evaluates each open position for hard exit triggers.
    Returns a dict of {ticker: reason} for positions that should be closed immediately.

    Checks:
      1. Stop-loss  — P&L <= -STOP_LOSS_PCT   (skipped if Alpaca bracket handles it)
      2. Take-profit — P&L >= TAKE_PROFIT_PCT  (skipped if Alpaca bracket handles it)
      3. Hold-period expiry — days held >= suggested hold_days (always checked)

    Positions entered with bracket orders have their stop-loss and take-profit managed
    server-side by Alpaca in real-time. The software check is skipped for those to
    avoid a double-close race condition; only hold-period expiry is checked.
    """
    to_close: dict[str, str] = {}
    today = __import__("datetime").date.today()

    for p in positions:
        ticker      = p["symbol"]
        pnl_pct     = float(p.get("unrealized_plpc", 0.0))
        has_bracket = meta.get(ticker, {}).get("has_bracket", False)

        if not has_bracket:
            # Software stop-loss and take-profit — only for positions without bracket legs
            if pnl_pct <= -STOP_LOSS_PCT:
                reason = f"stop-loss triggered: P&L {pnl_pct:+.1%} ≤ -{STOP_LOSS_PCT:.0%}"
                print(f"[risk] {ticker}: {reason}")
                to_close[ticker] = reason
                continue

            if pnl_pct >= TAKE_PROFIT_PCT:
                reason = f"take-profit triggered: P&L {pnl_pct:+.1%} ≥ +{TAKE_PROFIT_PCT:.0%}"
                print(f"[risk] {ticker}: {reason}")
                to_close[ticker] = reason
                continue
        else:
            print(f"[risk] {ticker}: bracket order active — SL/TP managed by Alpaca server-side")

        # Hold-period expiry — always checked regardless of bracket status
        if ticker in meta:
            entry_date = meta[ticker].get("entry_date")
            hold_days  = int(meta[ticker].get("hold_days", 30))
            if entry_date:
                try:
                    entry = __import__("datetime").date.fromisoformat(entry_date)
                    days_held = (today - entry).days
                    if days_held >= hold_days:
                        reason = f"hold period expired: held {days_held}d / target {hold_days}d"
                        print(f"[risk] {ticker}: {reason} — flagging for Claude review")
                        # Don't auto-close expired holds — pass to Claude for review
                        p["hold_expired"] = True
                        p["days_held"]    = days_held
                except ValueError:
                    pass

    return to_close
