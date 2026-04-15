"""
dashboard/app.py
Live trading dashboard — reads from Alpaca API + local log files.
Run with: streamlit run dashboard/app.py
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd

# ── Path setup so we can reuse broker.py ──────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from agent.broker import get_account, get_open_positions, is_market_open

# ── Config ────────────────────────────────────────────────────────────────────
LOGS_DIR  = ROOT / "logs"
META_FILE = LOGS_DIR / "positions_meta.json"

st.set_page_config(
    page_title="Trading Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Auto-refresh every 60 seconds
st.markdown(
    '<meta http-equiv="refresh" content="60">',
    unsafe_allow_html=True,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            return {}
    return {}


def _load_today_events() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"trades_{today}.jsonl"
    if not log_path.exists():
        return []
    events = []
    with open(log_path) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except Exception:
                pass
    return events


def _load_recent_events(days: int = 7) -> list[dict]:
    """Load events from the last N days of log files."""
    events = []
    for i in range(days):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        log_path = LOGS_DIR / f"trades_{d}.jsonl"
        if log_path.exists():
            with open(log_path) as f:
                for line in f:
                    try:
                        events.append(json.loads(line.strip()))
                    except Exception:
                        pass
    return events


def _pnl_color(val: float) -> str:
    if val > 0:
        return "color: #00c853"
    if val < 0:
        return "color: #ff1744"
    return ""


def _fmt_pnl(val: float, pct: bool = False) -> str:
    sym = "%" if pct else "$"
    sign = "+" if val >= 0 else ""
    if pct:
        return f"{sign}{val * 100:.2f}%"
    return f"{sign}${val:,.2f}"


# ── Header ────────────────────────────────────────────────────────────────────

st.title("📈 Congressional Trading Agent")
updated = datetime.now().strftime("%b %d %Y  %I:%M:%S %p")
market_status = "🟢 Market Open" if is_market_open() else "🔴 Market Closed"
st.caption(f"Last updated: {updated}  ·  {market_status}  ·  Auto-refreshes every 60s")
st.divider()

# ── Fetch live data ───────────────────────────────────────────────────────────

try:
    account   = get_account()
    positions = get_open_positions()
    meta      = _load_meta()
    fetch_ok  = True
except Exception as e:
    st.error(f"Could not connect to Alpaca: {e}")
    fetch_ok  = False
    account   = {}
    positions = []
    meta      = {}

# ── Account Summary ───────────────────────────────────────────────────────────

if fetch_ok:
    portfolio_val = account.get("portfolio_value", 0)
    cash          = account.get("cash", 0)
    equity        = account.get("equity", 0)

    # Compute today's P&L from log events
    today_events  = _load_today_events()
    realized_pnl  = sum(
        e.get("result", {}).get("realized_pnl") or 0
        for e in today_events
        if e.get("event") in ("hard_exit", "ai_close", "stop_loss_close", "take_profit_close")
        and e.get("result", {}).get("realized_pnl") is not None
    )
    unrealized_pnl = sum(float(p.get("unrealized_pl", 0)) for p in positions)
    total_day_pnl  = realized_pnl + unrealized_pnl

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Portfolio Value",  f"${portfolio_val:,.2f}")
    col2.metric("Cash Available",   f"${cash:,.2f}")
    col3.metric("Open Positions",   len(positions))
    col4.metric(
        "Unrealized P&L",
        f"${unrealized_pnl:+,.2f}",
        delta=f"${unrealized_pnl:+,.2f}",
        delta_color="normal",
    )
    col5.metric(
        "Today's Realized P&L",
        f"${realized_pnl:+,.2f}",
        delta=f"${realized_pnl:+,.2f}",
        delta_color="normal",
    )

    st.divider()

# ── Open Positions ────────────────────────────────────────────────────────────

st.subheader("Open Positions")

if not fetch_ok:
    st.warning("Alpaca connection failed — cannot load positions.")
elif not positions:
    st.info("No open positions.")
else:
    rows = []
    for p in positions:
        ticker     = p["symbol"]
        m          = meta.get(ticker, {})
        entry_px   = float(p.get("avg_entry_price", 0))
        current_px = float(p.get("current_price", 0))
        qty        = float(p.get("qty", 0))
        pnl_usd    = float(p.get("unrealized_pl", 0))
        pnl_pct    = float(p.get("unrealized_plpc", 0))

        stop_px  = m.get("stop_price")
        tp_px    = m.get("tp_price")
        bracket  = m.get("has_bracket", False)
        sector   = m.get("sector", "—")

        entry_date = m.get("entry_date", "—")
        hold_days  = m.get("hold_days", "—")
        days_held  = "—"
        if entry_date != "—":
            try:
                from datetime import date
                days_held = (date.today() - date.fromisoformat(entry_date)).days
            except Exception:
                pass

        rows.append({
            "Ticker":       ticker,
            "Qty":          qty,
            "Entry $":      f"${entry_px:.2f}",
            "Current $":    f"${current_px:.2f}",
            "P&L $":        pnl_usd,
            "P&L %":        pnl_pct,
            "Stop $":       f"${stop_px:.2f}" if stop_px else "—",
            "Target $":     f"${tp_px:.2f}"   if tp_px   else "—",
            "Bracket":      "✅" if bracket else "⚠️ manual",
            "Sector":       sector,
            "Entry Date":   entry_date,
            "Days Held":    days_held,
            "Hold Target":  hold_days,
        })

    df = pd.DataFrame(rows)

    def _color_pnl_row(row):
        styles = [""] * len(row)
        pnl_idx = df.columns.get_loc("P&L $")
        val = row.iloc[pnl_idx]
        color = "#1b4332" if val >= 0 else "#3b1a1a"
        return [f"background-color: {color}"] * len(row)

    styled = (
        df.style
        .format({"P&L $": "${:+,.2f}", "P&L %": "{:+.2%}"})
        .apply(_color_pnl_row, axis=1)
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

st.divider()

# ── Today's Activity ──────────────────────────────────────────────────────────

st.subheader("Today's Activity")

today_events = _load_today_events()
activity = [
    e for e in today_events
    if e.get("event") in (
        "order_placed", "hard_exit", "ai_close",
        "stop_loss_close", "take_profit_close",
        "daily_loss_halt", "skipped_signal_gate",
        "skipped_momentum", "skipped_liquidity",
        "skipped_earnings", "balance_sync",
    )
]

if not activity:
    st.info("No activity logged today yet.")
else:
    act_rows = []
    for e in reversed(activity):
        ts     = e.get("timestamp", "")[:19].replace("T", " ")
        event  = e.get("event", "")
        ticker = e.get("ticker") or e.get("order", {}).get("ticker", "—")
        result = e.get("result", {}) or {}
        order  = e.get("order",  {}) or {}

        if event == "order_placed":
            detail = (
                f"{order.get('action','').upper()} "
                f"${order.get('dollar_amount', 0):,.0f} — "
                f"conf {order.get('confidence', 0):.0%} | "
                f"bracket {'✅' if result.get('order_type') == 'bracket' else '—'}"
            )
            label = "🟢 Trade Placed"
        elif event in ("hard_exit", "ai_close", "stop_loss_close", "take_profit_close"):
            pnl = result.get("realized_pnl")
            pnl_str = f"P&L ${pnl:+,.2f}" if pnl is not None else ""
            reason = e.get("reason") or event.replace("_", " ")
            detail = f"{reason}  {pnl_str}"
            label  = "🔴 Position Closed"
        elif event == "daily_loss_halt":
            detail = f"Drawdown {e.get('drawdown', 0):.1%} hit {e.get('limit', 0):.0%} limit"
            label  = "🛑 Circuit Breaker"
        elif event in ("skipped_signal_gate", "skipped_momentum", "skipped_liquidity", "skipped_earnings"):
            detail = e.get("reason", "")
            label  = "⏭️ Skipped"
        elif event == "balance_sync":
            detail = f"Portfolio synced: ${e.get('portfolio_value', 0):,.2f}"
            label  = "🔄 Balance Sync"
        else:
            detail = ""
            label  = event

        act_rows.append({"Time": ts, "Event": label, "Ticker": ticker, "Detail": detail})

    st.dataframe(pd.DataFrame(act_rows), use_container_width=True, hide_index=True)

st.divider()

# ── Trade History (last 7 days) ───────────────────────────────────────────────

st.subheader("Trade History — Last 7 Days")

recent = _load_recent_events(days=7)
trades = [
    e for e in recent
    if e.get("event") == "order_placed"
]
closes = [
    e for e in recent
    if e.get("event") in ("hard_exit", "ai_close", "stop_loss_close", "take_profit_close")
]

if not trades and not closes:
    st.info("No trade history found in the last 7 days.")
else:
    hist_rows = []
    for e in sorted(trades + closes, key=lambda x: x.get("timestamp", ""), reverse=True):
        ts     = e.get("timestamp", "")[:10]
        event  = e.get("event", "")
        ticker = e.get("ticker") or e.get("order", {}).get("ticker", "—")
        order  = e.get("order",  {}) or {}
        result = e.get("result", {}) or {}

        if event == "order_placed":
            hist_rows.append({
                "Date":   ts,
                "Type":   "BUY",
                "Ticker": ticker,
                "Amount": f"${order.get('dollar_amount', 0):,.0f}",
                "Conf":   f"{order.get('confidence', 0):.0%}",
                "P&L":    "—",
                "Reason": order.get("reasoning", "")[:80],
            })
        else:
            pnl = result.get("realized_pnl")
            hist_rows.append({
                "Date":   ts,
                "Type":   "CLOSE",
                "Ticker": ticker,
                "Amount": "—",
                "Conf":   "—",
                "P&L":    f"${pnl:+,.2f}" if pnl is not None else "—",
                "Reason": e.get("reason", event.replace("_", " "))[:80],
            })

    st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)

st.divider()

# ── Signal Feed (recent AI analyses) ─────────────────────────────────────────

with st.expander("Recent AI Signal Analyses (last 24h)", expanded=False):
    analyses = [
        e for e in _load_recent_events(days=1)
        if e.get("event") == "analysis"
    ]
    if not analyses:
        st.info("No analyses logged in the last 24 hours.")
    else:
        sig_rows = []
        for e in reversed(analyses[-50:]):
            d      = e.get("decision", {})
            action = d.get("action", "—")
            icon   = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(action, "—")
            sig_rows.append({
                "Time":       e.get("timestamp", "")[:19].replace("T", " "),
                "Ticker":     e.get("ticker", "—"),
                "Action":     f"{icon} {action}",
                "Confidence": f"{d.get('confidence', 0):.0%}",
                "Risk":       d.get("risk_level", "—"),
                "Sector":     d.get("sector", "—"),
                "Reasoning":  d.get("reasoning", "")[:100],
            })
        st.dataframe(pd.DataFrame(sig_rows), use_container_width=True, hide_index=True)

# ── Footer ────────────────────────────────────────────────────────────────────

st.caption(
    f"Trading agent running · DRY_RUN={os.getenv('DRY_RUN', 'true')} · "
    f"Portfolio target $1,000,000 · "
    f"Data: Alpaca Markets"
)
