"""
notify.py
Email notifications via Gmail SMTP.
Sends portfolio updates, trade alerts, and daily summaries.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS  = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASSWORD", "")
NOTIFY_TO      = os.getenv("NOTIFY_EMAIL", GMAIL_ADDRESS)
NOTIFY_ENABLED = os.getenv("EMAIL_NOTIFICATIONS", "true").lower() == "true"


def _send(subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True on success."""
    if not NOTIFY_ENABLED:
        return False
    if not GMAIL_ADDRESS or not GMAIL_APP_PASS:
        print("[email] Gmail credentials not set — skipping notification.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = NOTIFY_TO
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
            server.sendmail(GMAIL_ADDRESS, NOTIFY_TO, msg.as_string())

        print(f"[email] Sent: {subject}")
        return True
    except Exception as e:
        print(f"[email] Failed to send '{subject}': {e}")
        return False


# ── Notification types ────────────────────────────────────────────────────────

def notify_order_placed(order: dict, result: dict) -> None:
    ticker     = order.get("ticker", "")
    action     = order.get("action", "").upper()
    amount     = order.get("dollar_amount", 0)
    confidence = order.get("confidence", 0)
    reasoning  = order.get("reasoning", "")
    sector     = order.get("sector", "")
    vol        = order.get("volatility", 0)
    order_type = result.get("order_type", "market")
    limit_px   = result.get("limit_price")

    price_line = f"Limit price:  ${limit_px:.2f}" if limit_px else "Order type:   Market"

    body = f"""New Trade Placed
{'=' * 40}
Action:       {action} {ticker}
Amount:       ${amount:,.2f}
{price_line}
Sector:       {sector}
Confidence:   {confidence:.0%}
Volatility:   {vol:.1%}
Order type:   {order_type.capitalize()}

Reasoning:
{reasoning}

Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
    _send(f"[Trading Agent] {action} {ticker} — ${amount:,.2f}", body)


def notify_position_closed(ticker: str, reason: str, result: dict) -> None:
    pnl     = result.get("realized_pnl")
    pnl_pct = result.get("realized_pct")

    pnl_line = ""
    if pnl is not None:
        emoji    = "+" if pnl >= 0 else ""
        pnl_line = f"Realized P&L: {emoji}${pnl:,.2f} ({pnl_pct:+.1%})"

    body = f"""Position Closed
{'=' * 40}
Ticker:  {ticker}
Reason:  {reason}
{pnl_line}

Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
    result_tag = f"{pnl_pct:+.1%}" if pnl_pct is not None else "closed"
    _send(f"[Trading Agent] Closed {ticker} — {result_tag}", body)


def notify_market_open(account: dict, positions: list[dict]) -> None:
    port_val = account.get("portfolio_value", 0)
    cash     = account.get("cash", 0)
    deployed = port_val - cash

    pos_lines = "\n".join(
        f"  {p['symbol']:6}  {p['qty']} shares  P&L {p['unrealized_plpc']:+.1%}  (${p['unrealized_pl']:+,.2f})"
        for p in positions
    ) or "  None"

    body = f"""Good morning — market is open.
{'=' * 40}
Portfolio value: ${port_val:>12,.2f}
Cash available:  ${cash:>12,.2f}
Deployed:        ${deployed:>12,.2f}

Open Positions ({len(positions)}):
{pos_lines}

Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
    _send(f"[Trading Agent] Market Open — Portfolio ${port_val:,.2f}", body)


def notify_end_of_day(account: dict, positions: list[dict], events: list[dict]) -> None:
    port_val = account.get("portfolio_value", 0)
    cash     = account.get("cash", 0)

    # Summarise today's closed trades from events
    closed = [e for e in events if e.get("event") in ("hard_exit", "ai_close", "stop_loss_close")]
    placed = [e for e in events if e.get("event") == "order_placed"]

    trades_placed = "\n".join(
        f"  BUY  {e['order']['ticker']:6}  ${e['order']['dollar_amount']:,.2f}"
        for e in placed
    ) or "  None"

    trades_closed = "\n".join(
        f"  SELL {e['ticker']:6}  {e.get('result', {}).get('realized_pct', 0):+.1%}  ({e.get('reason', e.get('event', ''))})"
        for e in closed
    ) or "  None"

    pos_lines = "\n".join(
        f"  {p['symbol']:6}  {p['qty']} shares  P&L {p['unrealized_plpc']:+.1%}  (${p['unrealized_pl']:+,.2f})"
        for p in positions
    ) or "  None"

    body = f"""End of Day Summary
{'=' * 40}
Portfolio value: ${port_val:>12,.2f}
Cash available:  ${cash:>12,.2f}

Trades Placed Today:
{trades_placed}

Positions Closed Today:
{trades_closed}

Open Positions ({len(positions)}):
{pos_lines}

Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
    _send(f"[Trading Agent] EOD Summary — Portfolio ${port_val:,.2f}", body)


def notify_daily_loss_halt(drawdown: float, limit: float) -> None:
    body = f"""ALERT: Daily Loss Limit Reached
{'=' * 40}
Drawdown:  {drawdown:.1%}
Limit:     {limit:.1%}

All new trades have been halted for the rest of the day.

Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
    _send(f"[Trading Agent] ALERT — Daily loss limit hit ({drawdown:.1%})", body)


def notify_bear_market(spy_price: float, ma50: float) -> None:
    body = f"""Market Regime Change: BEAR
{'=' * 40}
SPY is now trading below its 50-day moving average.
The agent has raised its confidence threshold for new trades.

SPY current: ${spy_price:.2f}
50-day MA:   ${ma50:.2f}

New positions will require higher conviction signals until SPY recovers above the MA.

Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
    _send("[Trading Agent] BEAR market detected — confidence threshold raised", body)
