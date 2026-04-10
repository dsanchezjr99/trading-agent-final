"""
risk_manager.py
Validates every AI decision before it reaches the broker.
Enforces position sizing, stop-loss levels, and exposure limits.
"""

import os
from dotenv import load_dotenv

load_dotenv()

MAX_POSITION_PCT   = float(os.getenv("MAX_POSITION_PCT", 0.15))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 5))
STOP_LOSS_PCT      = float(os.getenv("STOP_LOSS_PCT", 0.05))
MIN_CONFIDENCE     = float(os.getenv("MIN_CONFIDENCE", 0.70))
HARD_CAP_DOLLARS   = 75.0   # Absolute max per trade regardless of portfolio size


def evaluate(decision: dict, portfolio_value: float, open_positions: list[str]) -> tuple[bool, dict | None, str]:
    """
    Validate an AI trade decision.

    Returns:
        approved (bool)
        order (dict | None)  — order payload if approved
        reason (str)         — human-readable verdict
    """
    action     = decision.get("action", "HOLD").upper()
    ticker     = decision.get("ticker", "").upper()
    confidence = float(decision.get("confidence", 0.0))
    risk_level = decision.get("risk_level", "HIGH").upper()

    # ── Gate 1: HOLD passthrough ─────────────────────────────────────────────
    if action == "HOLD":
        return False, None, "AI decided HOLD — no order placed."

    # ── Gate 2: Minimum confidence ───────────────────────────────────────────
    if confidence < MIN_CONFIDENCE:
        return False, None, f"Confidence {confidence:.0%} below threshold {MIN_CONFIDENCE:.0%}."

    # ── Gate 3: Reject HIGH-risk signals ────────────────────────────────────
    if risk_level == "HIGH":
        return False, None, "Risk level HIGH — skipping to protect capital."

    # ── Gate 4: Max open positions ───────────────────────────────────────────
    if action == "BUY" and len(open_positions) >= MAX_OPEN_POSITIONS:
        return False, None, f"Already at max {MAX_OPEN_POSITIONS} open positions."

    # ── Gate 5: No duplicate positions ──────────────────────────────────────
    if action == "BUY" and ticker in open_positions:
        return False, None, f"Already holding {ticker} — skipping duplicate entry."

    # ── Gate 6: Must have a valid ticker ────────────────────────────────────
    if not ticker or len(ticker) > 5:
        return False, None, f"Invalid ticker: '{ticker}'."

    # ── Position sizing ───────────────────────────────────────────────────────
    raw_size = portfolio_value * MAX_POSITION_PCT
    # Scale down for lower confidence: 70% conf → 70% of max size
    confidence_scalar = (confidence - MIN_CONFIDENCE) / (1.0 - MIN_CONFIDENCE)
    scaled_size = raw_size * (0.5 + 0.5 * confidence_scalar)
    dollar_amount = min(scaled_size, HARD_CAP_DOLLARS)
    dollar_amount = round(dollar_amount, 2)

    if dollar_amount < 1.0:
        return False, None, "Calculated position size too small (< $1)."

    order = {
        "ticker":        ticker,
        "action":        action,
        "dollar_amount": dollar_amount,
        "stop_loss_pct": STOP_LOSS_PCT,
        "confidence":    confidence,
        "reasoning":     decision.get("reasoning", ""),
        "hold_days":     decision.get("suggested_hold_days", 5),
    }

    return True, order, (
        f"Approved: {action} ${dollar_amount:.2f} of {ticker} "
        f"(confidence {confidence:.0%}, risk {risk_level})"
    )


def check_stop_losses(positions: list[dict]) -> list[str]:
    """
    Given a list of open positions from the broker, return tickers
    that have breached the stop-loss threshold and should be sold.
    """
    to_close = []
    for p in positions:
        pnl_pct = float(p.get("unrealized_plpc", 0.0))
        if pnl_pct <= -STOP_LOSS_PCT:
            to_close.append(p["symbol"])
            print(
                f"[risk] Stop-loss triggered on {p['symbol']}: "
                f"P&L {pnl_pct:+.1%} ≤ -{STOP_LOSS_PCT:.0%}"
            )
    return to_close
