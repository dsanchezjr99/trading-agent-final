"""
dashboard/app.py
Live trading dashboard — Robinhood-inspired design.
Run with: streamlit run dashboard/app.py
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from agent.broker import get_account, get_open_positions, is_market_open

LOGS_DIR  = ROOT / "logs"
META_FILE = LOGS_DIR / "positions_meta.json"

st.set_page_config(
    page_title="Portfolio",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Robinhood CSS ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

* { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; }

html, body,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stHeader"],
.main { background-color: #0f0f0f !important; color: #fff !important; }

section[data-testid="stMainBlockContainer"] {
    padding: 0 2rem !important;
    max-width: 1100px;
    margin: 0 auto;
}
.block-container { padding-top: 1.5rem !important; padding-bottom: 2rem !important; }

/* Remove gaps */
[data-testid="stVerticalBlock"]   { gap: 0 !important; }
[data-testid="stHorizontalBlock"] { gap: 0 !important; align-items: center !important; }
[data-testid="column"]            { padding: 0 !important; }
.stMarkdown                       { margin: 0 !important; }
.stMarkdown p                     { margin: 0 !important; line-height: 1.5 !important; }

/* Hide Streamlit chrome */
[data-testid="stHeader"]      { display: none !important; }
[data-testid="stDecoration"]  { display: none !important; }
[data-testid="stToolbar"]     { display: none !important; }
footer                        { display: none !important; }
#MainMenu                     { display: none !important; }

/* Expander */
[data-testid="stExpander"] {
    background: transparent !important;
    border: none !important;
    border-top: 1px solid #222 !important;
    border-radius: 0 !important;
    padding: 0 !important;
}
[data-testid="stExpander"] summary {
    color: #888 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 14px 0 !important;
}
[data-testid="stExpander"] summary span[data-testid="stExpanderToggleIcon"] { display: none !important; }
details[data-testid="stExpander"] > summary > span:first-child { display: none !important; }

/* Scrollbar */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: #0f0f0f; }
::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
</style>
""", unsafe_allow_html=True)

st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

RH_GREEN = "#00c805"
RH_RED   = "#ff5000"

def _load_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            return {}
    return {}

def _load_events(days: int = 7) -> list[dict]:
    events = []
    for i in range(days):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        p = LOGS_DIR / f"trades_{d}.jsonl"
        if p.exists():
            with open(p) as f:
                for line in f:
                    try:
                        events.append(json.loads(line.strip()))
                    except Exception:
                        pass
    return events

def _pct_color(val: float) -> str:
    return RH_GREEN if val >= 0 else RH_RED

def _portfolio_chart(history: list[dict]) -> go.Figure | None:
    if len(history) < 2:
        return None
    df    = pd.DataFrame(history).drop_duplicates("date").sort_values("date")
    start = df["value"].iloc[0]
    end   = df["value"].iloc[-1]
    up    = end >= start

    fig = go.Figure()
    # Gradient fill
    fig.add_trace(go.Scatter(
        x    = df["date"].tolist() + df["date"].tolist()[::-1],
        y    = df["value"].tolist() + [start] * len(df),
        fill = "toself",
        fillcolor = "rgba(0,200,5,0.08)" if up else "rgba(255,80,0,0.08)",
        line = dict(width=0),
        hoverinfo = "skip",
        showlegend = False,
    ))
    # Main line
    fig.add_trace(go.Scatter(
        x    = df["date"],
        y    = df["value"],
        mode = "lines",
        line = dict(color=RH_GREEN if up else RH_RED, width=2),
        hovertemplate = "<b>%{x}</b><br>$%{y:,.2f}<extra></extra>",
        showlegend = False,
    ))
    fig.update_layout(
        height      = 200,
        margin      = dict(l=0, r=0, t=0, b=0),
        paper_bgcolor = "rgba(0,0,0,0)",
        plot_bgcolor  = "rgba(0,0,0,0)",
        xaxis = dict(visible=False),
        yaxis = dict(visible=False),
        hoverlabel = dict(bgcolor="#1a1a1a", font_color="#fff", bordercolor="#333"),
    )
    return fig


# ── Fetch data ────────────────────────────────────────────────────────────────

try:
    account   = get_account()
    positions = get_open_positions()
    meta      = _load_meta()
    fetch_ok  = True
except Exception as e:
    st.error(f"Cannot connect to Alpaca: {e}")
    fetch_ok  = False
    account   = {}
    positions = []
    meta      = {}

portfolio_val  = account.get("portfolio_value", 0)
cash           = account.get("cash", 0)
unrealized_pnl = sum(float(p.get("unrealized_pl", 0)) for p in positions)

today_events = _load_events(days=1)
realized_pnl = sum(
    e.get("result", {}).get("realized_pnl") or 0
    for e in today_events
    if e.get("event") in ("hard_exit", "ai_close", "stop_loss_close", "take_profit_close")
    and e.get("result", {}).get("realized_pnl") is not None
)
total_pnl    = unrealized_pnl + realized_pnl
pnl_pct      = (total_pnl / portfolio_val * 100) if portfolio_val else 0
pnl_color    = _pct_color(total_pnl)
pnl_sign     = "+" if total_pnl >= 0 else ""

market_open  = is_market_open()
target       = 1_000_000
progress_pct = min(portfolio_val / target * 100, 100)

history = []
for e in _load_events(days=90):
    if e.get("event") == "balance_sync":
        history.append({"date": e["timestamp"][:10], "value": e.get("portfolio_value", 0)})


# ── Portfolio hero ────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="padding: 24px 0 8px;">
    <div style="font-size: 13px; color: #888; margin-bottom: 6px; font-weight: 500;">
        Investing
    </div>
    <div style="font-size: 48px; font-weight: 700; color: #fff; letter-spacing: -1.5px; line-height: 1;">
        ${portfolio_val:,.2f}
    </div>
    <div style="font-size: 15px; font-weight: 500; color: {pnl_color}; margin-top: 8px;">
        {pnl_sign}${total_pnl:,.2f} ({pnl_sign}{pnl_pct:.2f}%) &nbsp;
        <span style="color: #888; font-weight: 400; font-size: 13px;">Today</span>
    </div>
</div>
""", unsafe_allow_html=True)

# Chart
chart = _portfolio_chart(history)
if chart:
    st.plotly_chart(chart, width="stretch", config={"displayModeBar": False})
else:
    st.markdown("""
    <div style="height:140px; border-bottom:1px solid #222;
                display:flex; align-items:center; justify-content:center;
                color:#333; font-size:13px;">
        Chart appears after first trading day
    </div>""", unsafe_allow_html=True)

# Progress toward $1M
st.markdown(f"""
<div style="margin: 16px 0 4px; display:flex; justify-content:space-between; align-items:center;">
    <span style="font-size:12px; color:#888;">Progress to $1,000,000</span>
    <span style="font-size:12px; color:{RH_GREEN}; font-weight:600;">{progress_pct:.2f}%</span>
</div>
<div style="background:#222; border-radius:2px; height:3px; margin-bottom:24px;">
    <div style="background:{RH_GREEN}; width:{progress_pct:.2f}%; height:100%; border-radius:2px;"></div>
</div>
""", unsafe_allow_html=True)


# ── Stats row ─────────────────────────────────────────────────────────────────

market_label = "Market open" if market_open else "Market closed"
market_dot   = RH_GREEN if market_open else "#888"
dry          = os.getenv("DRY_RUN", "true").lower() == "true"
mode_label   = "Paper trading" if dry else "Live trading"

st.markdown(f"""
<div style="display:flex; gap:32px; padding: 0 0 20px; border-bottom:1px solid #1e1e1e; flex-wrap:wrap;">
    <div>
        <div style="font-size:11px; color:#888; margin-bottom:3px;">Buying Power</div>
        <div style="font-size:16px; font-weight:600; color:#fff;">${cash:,.2f}</div>
    </div>
    <div>
        <div style="font-size:11px; color:#888; margin-bottom:3px;">Unrealized P&L</div>
        <div style="font-size:16px; font-weight:600; color:{_pct_color(unrealized_pnl)};">
            {'+' if unrealized_pnl >= 0 else ''}${unrealized_pnl:,.2f}
        </div>
    </div>
    <div>
        <div style="font-size:11px; color:#888; margin-bottom:3px;">Open Positions</div>
        <div style="font-size:16px; font-weight:600; color:#fff;">{len(positions)}</div>
    </div>
    <div>
        <div style="font-size:11px; color:#888; margin-bottom:3px;">Mode</div>
        <div style="font-size:16px; font-weight:600; color:#fff;">{mode_label}</div>
    </div>
    <div style="margin-left:auto; text-align:right;">
        <div style="font-size:12px; color:{market_dot}; font-weight:500;">● {market_label}</div>
        <div style="font-size:11px; color:#555; margin-top:2px;">{datetime.now().strftime('%I:%M %p')}</div>
    </div>
</div>
""", unsafe_allow_html=True)


# ── Positions ─────────────────────────────────────────────────────────────────

st.markdown("""
<div style="font-size:13px; font-weight:600; color:#888;
            letter-spacing:0.05em; text-transform:uppercase;
            padding: 20px 0 12px;">Stocks</div>
""", unsafe_allow_html=True)

if not fetch_ok:
    st.markdown('<p style="color:#555; font-size:14px;">Unable to load positions.</p>', unsafe_allow_html=True)
elif not positions:
    st.markdown('<p style="color:#555; font-size:14px; padding:12px 0;">No open positions.</p>', unsafe_allow_html=True)
else:
    for p in positions:
        ticker  = p["symbol"]
        m       = meta.get(ticker, {})
        qty     = float(p.get("qty", 0))
        entry   = float(p.get("avg_entry_price", 0))
        current = float(p.get("current_price", 0))
        pnl_usd = float(p.get("unrealized_pl", 0))
        pnl_pct = float(p.get("unrealized_plpc", 0)) * 100
        mkt_val = float(p.get("market_value", 0))
        sector  = m.get("sector", "")
        stop    = m.get("stop_price")
        tp      = m.get("tp_price")
        color   = _pct_color(pnl_usd)
        sign    = "+" if pnl_usd >= 0 else ""

        col_left, col_right = st.columns([3, 1])
        with col_left:
            st.markdown(f"""
<div style="padding: 14px 0;">
    <div style="display:flex; align-items:baseline; gap:10px;">
        <span style="font-size:16px; font-weight:700; color:#fff;">{ticker}</span>
        <span style="font-size:12px; color:#555;">{qty:g} shares · {sector}</span>
    </div>
    <div style="font-size:12px; color:#555; margin-top:3px;">
        Avg ${entry:.2f}
        {"· Stop $" + f"{stop:.2f}" if stop else ""}
        {"· Target $" + f"{tp:.2f}" if tp else ""}
    </div>
</div>""", unsafe_allow_html=True)

        with col_right:
            st.markdown(f"""
<div style="text-align:right; padding: 14px 0;">
    <div style="font-size:16px; font-weight:600; color:#fff;">${current:.2f}</div>
    <div style="font-size:13px; font-weight:500; color:{color};">
        {sign}${pnl_usd:,.2f} ({sign}{pnl_pct:.2f}%)
    </div>
</div>""", unsafe_allow_html=True)

        st.markdown('<div style="height:1px; background:#1e1e1e;"></div>', unsafe_allow_html=True)


# ── Activity list ─────────────────────────────────────────────────────────────

st.markdown("""
<div style="font-size:13px; font-weight:600; color:#888;
            letter-spacing:0.05em; text-transform:uppercase;
            padding: 28px 0 12px;">Activity</div>
""", unsafe_allow_html=True)

activity_events = [
    e for e in _load_events(days=7)
    if e.get("event") in (
        "order_placed", "hard_exit", "ai_close",
        "stop_loss_close", "take_profit_close",
        "daily_loss_halt",
    )
]
activity_events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

if not activity_events:
    st.markdown('<p style="color:#555; font-size:14px; padding:8px 0;">No recent activity.</p>', unsafe_allow_html=True)
else:
    for e in activity_events[:40]:
        event  = e.get("event", "")
        ts_raw = e.get("timestamp", "")[:19].replace("T", " ")
        ts_fmt = ts_raw[5:16]  # MM-DD HH:MM
        ticker = e.get("ticker") or (e.get("order") or {}).get("ticker", "—")
        order  = e.get("order",  {}) or {}
        result = e.get("result", {}) or {}
        pnl    = result.get("realized_pnl")
        is_buy = event == "order_placed"

        if is_buy:
            amt      = order.get("dollar_amount", 0)
            conf     = order.get("confidence", 0)
            label    = "Bought"
            sublabel = f"${amt:,.0f} · Conf {conf:.0%}"
            val_str  = f"${amt:,.0f}"
            val_col  = "#fff"
        else:
            reason   = e.get("reason", event.replace("_", " ")).replace("triggered:", "·")
            label    = "Sold"
            sublabel = reason[:60]
            val_str  = (f'{"+" if pnl and pnl >= 0 else ""}${pnl:,.2f}') if pnl is not None else "—"
            val_col  = _pct_color(pnl) if pnl is not None else "#888"

        col_l, col_r = st.columns([3, 1])
        with col_l:
            st.markdown(f"""
<div style="padding:13px 0;">
    <div style="font-size:15px; font-weight:600; color:#fff;">{label} {ticker}</div>
    <div style="font-size:12px; color:#555; margin-top:2px;">{sublabel}</div>
</div>""", unsafe_allow_html=True)
        with col_r:
            st.markdown(f"""
<div style="text-align:right; padding:13px 0;">
    <div style="font-size:15px; font-weight:600; color:{val_col};">{val_str}</div>
    <div style="font-size:11px; color:#444; margin-top:2px;">{ts_fmt}</div>
</div>""", unsafe_allow_html=True)

        st.markdown('<div style="height:1px; background:#1e1e1e;"></div>', unsafe_allow_html=True)


# ── Signal analyses ───────────────────────────────────────────────────────────

with st.expander("Recent AI Signals"):
    analyses = [e for e in _load_events(days=1) if e.get("event") == "analysis"]
    if not analyses:
        st.caption("No signals in the last 24 hours.")
    else:
        for e in reversed(analyses[-30:]):
            d      = e.get("decision", {})
            action = d.get("action", "HOLD")
            conf   = d.get("confidence", 0)
            ticker = e.get("ticker", "—")
            ts     = e.get("timestamp", "")[:19][11:16]
            reason = d.get("reasoning", "")[:120]
            color  = RH_GREEN if action == "BUY" else RH_RED if action == "SELL" else "#888"

            col_l, col_r = st.columns([3, 1])
            with col_l:
                st.markdown(f"""
<div style="padding:10px 0;">
    <div style="font-size:14px; font-weight:600; color:#fff;">
        {ticker} &nbsp;<span style="color:{color}; font-size:12px;">{action}</span>
    </div>
    <div style="font-size:11px; color:#555; margin-top:2px;">{reason}</div>
</div>""", unsafe_allow_html=True)
            with col_r:
                st.markdown(f"""
<div style="text-align:right; padding:10px 0;">
    <div style="font-size:13px; color:{color}; font-weight:600;">{conf:.0%}</div>
    <div style="font-size:11px; color:#444;">{ts}</div>
</div>""", unsafe_allow_html=True)
            st.markdown('<div style="height:1px; background:#1e1e1e;"></div>', unsafe_allow_html=True)


# ── Footer ────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="padding:24px 0 8px; font-size:11px; color:#333; text-align:center;">
    Congressional Trading Agent &nbsp;·&nbsp; Target $1,000,000 &nbsp;·&nbsp;
    {datetime.now().strftime('%b %d, %Y %I:%M %p')}
</div>
""", unsafe_allow_html=True)
