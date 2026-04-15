"""
dashboard/app.py
Live trading dashboard — TradingView-inspired dark theme.
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

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Trading Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── TradingView-style CSS ─────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Base ── */
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: #131722 !important;
    color: #d1d4dc !important;
}
[data-testid="stHeader"] { background-color: #131722 !important; }
[data-testid="stSidebar"] { background-color: #1e222d !important; }
section[data-testid="stMainBlockContainer"] { padding-top: 1rem !important; }

/* ── Typography ── */
h1, h2, h3, h4, label, p, span, div {
    color: #d1d4dc !important;
    font-family: -apple-system, BlinkMacSystemFont, "Trebuchet MS", sans-serif !important;
}

/* ── Cards ── */
.tv-card {
    background: #1e222d;
    border: 1px solid #2a2e39;
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 12px;
}
.tv-card-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #787b86 !important;
    margin-bottom: 6px;
}
.tv-card-value {
    font-size: 26px;
    font-weight: 700;
    color: #d1d4dc !important;
    line-height: 1.2;
}
.tv-card-value-sm {
    font-size: 18px;
    font-weight: 600;
    color: #d1d4dc !important;
}
.pos { color: #26a69a !important; }
.neg { color: #ef5350 !important; }
.neutral { color: #787b86 !important; }

/* ── Metric overrides ── */
[data-testid="stMetric"] {
    background: #1e222d;
    border: 1px solid #2a2e39;
    border-radius: 8px;
    padding: 14px 18px !important;
}
[data-testid="stMetricLabel"] { color: #787b86 !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: 0.08em; }
[data-testid="stMetricValue"] { color: #d1d4dc !important; font-size: 22px !important; font-weight: 700 !important; }
[data-testid="stMetricDelta"] svg { display: none; }
[data-testid="stMetricDelta"] > div { font-size: 13px !important; font-weight: 600 !important; }

/* ── Tables / DataFrames ── */
[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; border: 1px solid #2a2e39; }
.dvn-scroller { background: #1e222d !important; }
thead tr th {
    background: #1e222d !important;
    color: #787b86 !important;
    font-size: 11px !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    border-bottom: 1px solid #2a2e39 !important;
}
tbody tr td { color: #d1d4dc !important; font-size: 13px !important; border-bottom: 1px solid #2a2e39 !important; }
tbody tr:hover td { background: #2a2e39 !important; }

/* ── Expanders ── */
[data-testid="stExpander"] {
    background: #1e222d !important;
    border: 1px solid #2a2e39 !important;
    border-radius: 8px !important;
}
[data-testid="stExpander"] summary { color: #d1d4dc !important; }

/* ── Dividers ── */
hr { border-color: #2a2e39 !important; margin: 8px 0 !important; }

/* ── Status badges ── */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
}
.badge-green { background: rgba(38,166,154,0.15); color: #26a69a; }
.badge-red   { background: rgba(239,83,80,0.15);  color: #ef5350; }
.badge-gray  { background: rgba(120,123,134,0.15); color: #787b86; }

/* ── Info / warning boxes ── */
[data-testid="stInfo"], [data-testid="stWarning"], [data-testid="stError"] {
    background: #1e222d !important;
    border-left-color: #2a2e39 !important;
    color: #787b86 !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #131722; }
::-webkit-scrollbar-thumb { background: #2a2e39; border-radius: 3px; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            return {}
    return {}


def _load_events(days: int = 1) -> list[dict]:
    events = []
    for i in range(days):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        path = LOGS_DIR / f"trades_{d}.jsonl"
        if path.exists():
            with open(path) as f:
                for line in f:
                    try:
                        events.append(json.loads(line.strip()))
                    except Exception:
                        pass
    return events


def _pnl_html(val: float, pct: bool = False) -> str:
    cls = "pos" if val >= 0 else "neg"
    sign = "+" if val >= 0 else ""
    if pct:
        return f'<span class="{cls}">{sign}{val * 100:.2f}%</span>'
    return f'<span class="{cls}">{sign}${val:,.2f}</span>'


def _portfolio_history() -> list[dict]:
    """Build a list of {date, portfolio_value} from balance_sync log events."""
    points = []
    for e in _load_events(days=90):
        if e.get("event") == "balance_sync":
            points.append({
                "date":  e["timestamp"][:10],
                "value": e.get("portfolio_value", 0),
            })
    return points


def _sparkline(values: list[float], color: str = "#26a69a") -> go.Figure:
    fig = go.Figure(go.Scatter(
        y=values,
        mode="lines",
        line=dict(color=color, width=2),
        fill="tozeroy",
        fillcolor=color.replace(")", ", 0.08)").replace("rgb", "rgba") if "rgb" in color
                  else f"rgba(38,166,154,0.08)",
        hoverinfo="skip",
    ))
    fig.update_layout(
        height=80,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        showlegend=False,
    )
    return fig


def _pnl_bar_chart(positions: list[dict]) -> go.Figure:
    if not positions:
        return None
    tickers = [p["symbol"] for p in positions]
    pnls    = [float(p.get("unrealized_plpc", 0)) * 100 for p in positions]
    colors  = ["#26a69a" if v >= 0 else "#ef5350" for v in pnls]

    fig = go.Figure(go.Bar(
        x=tickers,
        y=pnls,
        marker_color=colors,
        marker_line_width=0,
        text=[f"{v:+.1f}%" for v in pnls],
        textposition="outside",
        textfont=dict(color="#d1d4dc", size=11),
    ))
    fig.update_layout(
        height=200,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(color="#787b86", showgrid=False, tickfont=dict(size=11)),
        yaxis=dict(color="#787b86", showgrid=True, gridcolor="#2a2e39",
                   ticksuffix="%", tickfont=dict(size=10)),
        showlegend=False,
    )
    return fig


def _portfolio_chart(history: list[dict]) -> go.Figure | None:
    if len(history) < 2:
        return None
    df = pd.DataFrame(history).drop_duplicates("date").sort_values("date")
    start = df["value"].iloc[0]
    color = "#26a69a" if df["value"].iloc[-1] >= start else "#ef5350"

    fig = go.Figure(go.Scatter(
        x=df["date"],
        y=df["value"],
        mode="lines",
        line=dict(color=color, width=2),
        fill="tozeroy",
        fillcolor=f"rgba(38,166,154,0.07)" if color == "#26a69a" else "rgba(239,83,80,0.07)",
        hovertemplate="<b>%{x}</b><br>$%{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        height=180,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(color="#787b86", showgrid=False, tickfont=dict(size=10)),
        yaxis=dict(color="#787b86", showgrid=True, gridcolor="#2a2e39",
                   tickprefix="$", tickformat=",.0f", tickfont=dict(size=10)),
        showlegend=False,
        hoverlabel=dict(bgcolor="#2a2e39", font_color="#d1d4dc", bordercolor="#2a2e39"),
    )
    return fig


# ── Fetch live data ───────────────────────────────────────────────────────────

try:
    account   = get_account()
    positions = get_open_positions()
    meta      = _load_meta()
    fetch_ok  = True
except Exception as e:
    st.error(f"Alpaca connection failed: {e}")
    fetch_ok  = False
    account   = {}
    positions = []
    meta      = {}

market_open    = is_market_open()
portfolio_val  = account.get("portfolio_value", 0)
cash           = account.get("cash", 0)
unrealized_pnl = sum(float(p.get("unrealized_pl", 0)) for p in positions)

today_events   = _load_events(days=1)
realized_pnl   = sum(
    e.get("result", {}).get("realized_pnl") or 0
    for e in today_events
    if e.get("event") in ("hard_exit", "ai_close", "stop_loss_close", "take_profit_close")
    and e.get("result", {}).get("realized_pnl") is not None
)

history        = _portfolio_history()
target         = 1_000_000
progress_pct   = min(portfolio_val / target * 100, 100)


# ── Header ────────────────────────────────────────────────────────────────────

market_badge = (
    '<span class="badge badge-green">● MARKET OPEN</span>'
    if market_open else
    '<span class="badge badge-gray">● MARKET CLOSED</span>'
)
updated = datetime.now().strftime("%b %d %Y  %I:%M:%S %p")

st.markdown(f"""
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
    <span style="font-size:20px; font-weight:700; color:#d1d4dc; letter-spacing:-0.3px;">
        📈 Congressional Trading Agent
    </span>
    <span style="font-size:12px; color:#787b86;">
        {market_badge} &nbsp;·&nbsp; {updated} &nbsp;·&nbsp; Auto-refreshes every 60s
    </span>
</div>
""", unsafe_allow_html=True)

st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)
st.markdown('<hr>', unsafe_allow_html=True)


# ── Top metrics row ───────────────────────────────────────────────────────────

c1, c2, c3, c4, c5, c6 = st.columns(6)

with c1:
    st.metric("Portfolio Value", f"${portfolio_val:,.2f}")
with c2:
    st.metric("Cash", f"${cash:,.2f}")
with c3:
    st.metric("Open Positions", len(positions))
with c4:
    sign = "+" if unrealized_pnl >= 0 else ""
    st.metric("Unrealized P&L", f"{sign}${unrealized_pnl:,.2f}",
              delta=f"{sign}${unrealized_pnl:,.2f}")
with c5:
    sign = "+" if realized_pnl >= 0 else ""
    st.metric("Today's Realized P&L", f"{sign}${realized_pnl:,.2f}",
              delta=f"{sign}${realized_pnl:,.2f}")
with c6:
    st.metric("Target Progress", f"{progress_pct:.1f}%",
              delta=f"${1_000_000 - portfolio_val:,.0f} to go")

st.markdown("<br>", unsafe_allow_html=True)


# ── Progress bar toward $1M ───────────────────────────────────────────────────

st.markdown(f"""
<div class="tv-card">
    <div class="tv-card-title">Progress to $1,000,000</div>
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <span style="font-size:13px; color:#787b86;">
            ${portfolio_val:,.2f} &nbsp;/&nbsp; $1,000,000
        </span>
        <span class="pos" style="font-size:13px; font-weight:600;">{progress_pct:.2f}%</span>
    </div>
    <div style="background:#2a2e39; border-radius:4px; height:8px; overflow:hidden;">
        <div style="background:linear-gradient(90deg,#26a69a,#00bcd4); width:{progress_pct:.2f}%;
                    height:100%; border-radius:4px; transition:width 0.5s;"></div>
    </div>
</div>
""", unsafe_allow_html=True)


# ── Main layout: left (charts + positions) | right (activity + history) ───────

left, right = st.columns([2, 1], gap="medium")

with left:

    # ── Portfolio value chart ────────────────────────────────────────────────
    st.markdown('<div class="tv-card-title" style="margin-bottom:4px;">PORTFOLIO VALUE HISTORY</div>', unsafe_allow_html=True)
    chart = _portfolio_chart(history)
    if chart:
        st.plotly_chart(chart, use_container_width=True, config={"displayModeBar": False})
    else:
        st.markdown("""
        <div style="background:#1e222d; border:1px solid #2a2e39; border-radius:8px;
                    padding:24px; text-align:center; color:#787b86; font-size:13px;">
            Chart populates after first end-of-day balance sync
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Open positions ───────────────────────────────────────────────────────
    st.markdown('<div class="tv-card-title" style="margin-bottom:8px;">OPEN POSITIONS</div>', unsafe_allow_html=True)

    if not fetch_ok:
        st.warning("Cannot load positions — Alpaca connection failed.")
    elif not positions:
        st.markdown("""
        <div style="background:#1e222d; border:1px solid #2a2e39; border-radius:8px;
                    padding:20px; text-align:center; color:#787b86; font-size:13px;">
            No open positions
        </div>""", unsafe_allow_html=True)
    else:
        # P&L bar chart
        bar = _pnl_bar_chart(positions)
        if bar:
            st.plotly_chart(bar, use_container_width=True, config={"displayModeBar": False})

        # Positions table
        rows = []
        for p in positions:
            ticker  = p["symbol"]
            m       = meta.get(ticker, {})
            entry   = float(p.get("avg_entry_price", 0))
            current = float(p.get("current_price", 0))
            qty     = float(p.get("qty", 0))
            pnl_usd = float(p.get("unrealized_pl", 0))
            pnl_pct = float(p.get("unrealized_plpc", 0))
            stop    = m.get("stop_price")
            tp      = m.get("tp_price")
            bracket = m.get("has_bracket", False)
            sector  = m.get("sector", "—")

            entry_date = m.get("entry_date", "—")
            days_held  = "—"
            if entry_date != "—":
                try:
                    days_held = (date.today() - date.fromisoformat(entry_date)).days
                except Exception:
                    pass

            rows.append({
                "Ticker":     ticker,
                "Qty":        qty,
                "Entry":      f"${entry:.2f}",
                "Price":      f"${current:.2f}",
                "P&L $":      pnl_usd,
                "P&L %":      pnl_pct,
                "Stop":       f"${stop:.2f}" if stop else "—",
                "Target":     f"${tp:.2f}"   if tp   else "—",
                "Bracket":    "✅" if bracket else "⚠️",
                "Sector":     sector,
                "Days Held":  days_held,
            })

        df = pd.DataFrame(rows)

        def _style_row(row):
            pnl_idx = df.columns.get_loc("P&L $")
            val = row.iloc[pnl_idx]
            bg = "background-color: #1a2e28" if val >= 0 else "background-color: #2e1a1a"
            return [bg] * len(row)

        styled = (
            df.style
            .apply(_style_row, axis=1)
            .format({"P&L $": "${:+,.2f}", "P&L %": "{:+.2%}"})
        )
        st.dataframe(styled, use_container_width=True, hide_index=True, height=min(40 * len(rows) + 38, 300))


with right:

    # ── Account stats card ────────────────────────────────────────────────────
    dry = os.getenv("DRY_RUN", "true").lower() == "true"
    mode_badge = '<span class="badge badge-gray">PAPER</span>' if dry else '<span class="badge badge-green">LIVE</span>'
    regime_events = [e for e in today_events if e.get("event") == "bear_market"]
    regime_badge = (
        '<span class="badge badge-red">BEAR</span>'
        if regime_events else
        '<span class="badge badge-green">BULL</span>'
    )

    st.markdown(f"""
    <div class="tv-card">
        <div class="tv-card-title">Account</div>
        <div style="display:flex; gap:8px; margin-bottom:12px;">{mode_badge} {regime_badge}</div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
            <div>
                <div style="font-size:10px; color:#787b86; text-transform:uppercase;">Portfolio</div>
                <div style="font-size:15px; font-weight:700;">${portfolio_val:,.2f}</div>
            </div>
            <div>
                <div style="font-size:10px; color:#787b86; text-transform:uppercase;">Cash</div>
                <div style="font-size:15px; font-weight:700;">${cash:,.2f}</div>
            </div>
            <div>
                <div style="font-size:10px; color:#787b86; text-transform:uppercase;">Unrealized</div>
                <div style="font-size:15px; font-weight:700;" class="{'pos' if unrealized_pnl >= 0 else 'neg'}">{'+' if unrealized_pnl >= 0 else ''}${unrealized_pnl:,.2f}</div>
            </div>
            <div>
                <div style="font-size:10px; color:#787b86; text-transform:uppercase;">Realized Today</div>
                <div style="font-size:15px; font-weight:700;" class="{'pos' if realized_pnl >= 0 else 'neg'}">{'+' if realized_pnl >= 0 else ''}${realized_pnl:,.2f}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Today's activity feed ────────────────────────────────────────────────
    st.markdown('<div class="tv-card-title" style="margin:12px 0 6px;">TODAY\'S ACTIVITY</div>', unsafe_allow_html=True)

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
        st.markdown('<div style="color:#787b86; font-size:13px; padding:8px 0;">No activity yet today.</div>', unsafe_allow_html=True)
    else:
        feed_html = '<div style="display:flex; flex-direction:column; gap:6px;">'
        for e in reversed(activity[-20:]):
            ts     = e.get("timestamp", "")[:19][11:]   # HH:MM:SS
            event  = e.get("event", "")
            ticker = e.get("ticker") or (e.get("order") or {}).get("ticker", "—")
            result = e.get("result", {}) or {}
            order  = e.get("order",  {}) or {}

            if event == "order_placed":
                amt   = order.get("dollar_amount", 0)
                conf  = order.get("confidence", 0)
                label = f'<span class="badge badge-green">BUY</span>'
                detail = f"${amt:,.0f} · conf {conf:.0%}"
            elif event in ("hard_exit", "ai_close", "stop_loss_close", "take_profit_close"):
                pnl    = result.get("realized_pnl")
                pnl_s  = f"{'+'if pnl and pnl>=0 else ''}${pnl:,.2f}" if pnl is not None else ""
                cls    = "pos" if pnl and pnl >= 0 else "neg"
                label  = f'<span class="badge badge-red">CLOSE</span>'
                detail = f'<span class="{cls}">{pnl_s}</span> · {event.replace("_"," ")}'
            elif event == "daily_loss_halt":
                label  = f'<span class="badge badge-red">HALT</span>'
                detail = f"Circuit breaker triggered"
            elif event in ("skipped_signal_gate", "skipped_momentum", "skipped_liquidity", "skipped_earnings"):
                label  = f'<span class="badge badge-gray">SKIP</span>'
                detail = event.replace("skipped_", "").replace("_", " ")
            elif event == "balance_sync":
                label  = f'<span class="badge badge-gray">SYNC</span>'
                detail = f"${e.get('portfolio_value',0):,.2f}"
                ticker = "—"
            else:
                label  = f'<span class="badge badge-gray">{event[:6].upper()}</span>'
                detail = ""

            feed_html += f"""
            <div style="background:#1e222d; border:1px solid #2a2e39; border-radius:6px;
                        padding:8px 10px; display:flex; justify-content:space-between; align-items:center;">
                <div style="display:flex; align-items:center; gap:8px;">
                    {label}
                    <span style="font-size:13px; font-weight:600; color:#d1d4dc;">{ticker}</span>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:12px; color:#d1d4dc;">{detail}</div>
                    <div style="font-size:10px; color:#787b86;">{ts}</div>
                </div>
            </div>"""
        feed_html += "</div>"
        st.markdown(feed_html, unsafe_allow_html=True)


# ── Trade history ─────────────────────────────────────────────────────────────

st.markdown("<br>", unsafe_allow_html=True)
st.markdown('<div class="tv-card-title" style="margin-bottom:8px;">TRADE HISTORY — LAST 7 DAYS</div>', unsafe_allow_html=True)

recent = _load_events(days=7)
trades = [e for e in recent if e.get("event") == "order_placed"]
closes = [e for e in recent if e.get("event") in ("hard_exit", "ai_close", "stop_loss_close", "take_profit_close")]

if not trades and not closes:
    st.markdown('<div style="color:#787b86; font-size:13px; padding:8px 0;">No trade history in the last 7 days.</div>', unsafe_allow_html=True)
else:
    hist = []
    for e in sorted(trades + closes, key=lambda x: x.get("timestamp", ""), reverse=True):
        ts     = e.get("timestamp", "")[:19].replace("T", " ")
        event  = e.get("event", "")
        ticker = e.get("ticker") or (e.get("order") or {}).get("ticker", "—")
        order  = e.get("order",  {}) or {}
        result = e.get("result", {}) or {}
        pnl    = result.get("realized_pnl")

        hist.append({
            "Time":    ts,
            "Type":    "BUY" if event == "order_placed" else "CLOSE",
            "Ticker":  ticker,
            "Amount":  f"${order.get('dollar_amount',0):,.0f}" if event == "order_placed" else "—",
            "Conf":    f"{order.get('confidence',0):.0%}"      if event == "order_placed" else "—",
            "P&L":     f"{'+'if pnl and pnl>=0 else ''}${pnl:,.2f}" if pnl is not None else "—",
            "Reason":  (order.get("reasoning","") if event == "order_placed"
                        else e.get("reason", event.replace("_"," ")))[:90],
        })

    df_hist = pd.DataFrame(hist)

    def _color_hist(row):
        t = row["Type"]
        if t == "BUY":
            return ["background-color: #1a2e28"] * len(row)
        return ["background-color: #2e1a1a"] * len(row)

    st.dataframe(
        df_hist.style.apply(_color_hist, axis=1),
        use_container_width=True,
        hide_index=True,
        height=min(42 * len(hist) + 38, 320),
    )


# ── Signal analyses (expandable) ─────────────────────────────────────────────

with st.expander("AI Signal Analyses — Last 24h", expanded=False):
    analyses = [e for e in _load_events(days=1) if e.get("event") == "analysis"]
    if not analyses:
        st.markdown('<span style="color:#787b86; font-size:13px;">No analyses in the last 24 hours.</span>', unsafe_allow_html=True)
    else:
        sig_rows = []
        for e in reversed(analyses[-50:]):
            d      = e.get("decision", {})
            action = d.get("action", "—")
            sig_rows.append({
                "Time":       e.get("timestamp","")[:19].replace("T"," "),
                "Ticker":     e.get("ticker","—"),
                "Action":     action,
                "Confidence": f"{d.get('confidence',0):.0%}",
                "Risk":       d.get("risk_level","—"),
                "Sector":     d.get("sector","—"),
                "Reasoning":  d.get("reasoning","")[:100],
            })

        def _color_sig(row):
            a = row["Action"]
            if a == "BUY":
                return ["background-color: #1a2e28"] * len(row)
            if a == "SELL":
                return ["background-color: #2e1a1a"] * len(row)
            return ["background-color: #1e222d"] * len(row)

        st.dataframe(
            pd.DataFrame(sig_rows).style.apply(_color_sig, axis=1),
            use_container_width=True,
            hide_index=True,
        )

# ── Footer ────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="text-align:center; color:#787b86; font-size:11px; margin-top:16px; padding-top:12px; border-top:1px solid #2a2e39;">
    Congressional Trading Agent &nbsp;·&nbsp;
    DRY_RUN={os.getenv('DRY_RUN','true')} &nbsp;·&nbsp;
    Target $1,000,000 &nbsp;·&nbsp;
    Data: Alpaca Markets
</div>
""", unsafe_allow_html=True)
