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
section[data-testid="stMainBlockContainer"] { padding-top: 0.5rem !important; padding-bottom: 0.5rem !important; }

/* Kill Streamlit's default vertical gap between elements */
.block-container { padding-top: 0.5rem !important; gap: 0 !important; }
[data-testid="stVerticalBlock"] { gap: 0.25rem !important; }
[data-testid="stHorizontalBlock"] { gap: 0.4rem !important; }

/* Tighten column padding */
[data-testid="column"] { padding: 0 4px !important; }

/* Remove extra space Streamlit adds around markdown */
.stMarkdown { margin-bottom: 0 !important; }
.stMarkdown p { margin: 0 !important; line-height: 1.4 !important; }

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
[data-testid="stExpander"] summary {
    color: #d1d4dc !important;
    font-size: 12px !important;
    font-weight: 600 !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
}
/* Hide the raw "_arrow_right" icon text that leaks in some Streamlit versions */
[data-testid="stExpander"] summary svg { display: none !important; }
[data-testid="stExpander"] summary span[data-testid="stExpanderToggleIcon"] { display: none !important; }
details[data-testid="stExpander"] > summary > span:first-child { display: none !important; }

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


def _portfolio_chart(history: list[dict], height: int = 260) -> go.Figure | None:
    if len(history) < 2:
        return None
    df    = pd.DataFrame(history).drop_duplicates("date").sort_values("date")
    start = df["value"].iloc[0]
    end   = df["value"].iloc[-1]
    color = "#26a69a" if end >= start else "#ef5350"
    fill  = "rgba(38,166,154,0.12)" if end >= start else "rgba(239,83,80,0.12)"

    # Baseline: flat line at start value for the fill to look like TV
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"].tolist() + df["date"].tolist()[::-1],
        y=df["value"].tolist() + [start] * len(df),
        fill="toself",
        fillcolor=fill,
        line=dict(width=0),
        hoverinfo="skip",
        showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["value"],
        mode="lines",
        line=dict(color=color, width=2.5),
        hovertemplate="<b>%{x}</b><br>$%{y:,.2f}<extra></extra>",
        showlegend=False,
    ))
    # Dot at the end
    fig.add_trace(go.Scatter(
        x=[df["date"].iloc[-1]],
        y=[end],
        mode="markers",
        marker=dict(color=color, size=8, line=dict(color="#131722", width=2)),
        hoverinfo="skip",
        showlegend=False,
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            color="#787b86", showgrid=False,
            tickfont=dict(size=10, color="#787b86"),
            tickformat="%b %d", showline=False, zeroline=False,
        ),
        yaxis=dict(
            color="#787b86", showgrid=True, gridcolor="#2a2e39",
            tickprefix="$", tickformat=",.0f", tickfont=dict(size=10, color="#787b86"),
            showline=False, zeroline=False, side="right",
        ),
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


# ── Auto-refresh ─────────────────────────────────────────────────────────────
st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)

# ── Navbar ────────────────────────────────────────────────────────────────────
market_badge = (
    '<span class="badge badge-green">● MARKET OPEN</span>'
    if market_open else
    '<span class="badge badge-gray">● MARKET CLOSED</span>'
)
dry   = os.getenv("DRY_RUN", "true").lower() == "true"
mode  = '<span class="badge badge-gray">PAPER</span>' if dry else '<span class="badge badge-green">LIVE</span>'
updated = datetime.now().strftime("%b %d %Y  %I:%M:%S %p")

st.markdown(f"""
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
    <span style="font-size:16px; font-weight:700; color:#d1d4dc; letter-spacing:-0.3px;">
        Congressional Trading Agent
    </span>
    <span style="font-size:12px; color:#787b86; display:flex; gap:10px; align-items:center;">
        {mode} {market_badge} &nbsp;·&nbsp; {updated} &nbsp;·&nbsp; refreshes every 60s
    </span>
</div>
""", unsafe_allow_html=True)


# ── Hero card: portfolio value + chart ───────────────────────────────────────

pnl_color   = "#26a69a" if unrealized_pnl >= 0 else "#ef5350"
pnl_sign    = "+" if unrealized_pnl >= 0 else ""
total_pnl   = unrealized_pnl + realized_pnl
total_color = "#26a69a" if total_pnl >= 0 else "#ef5350"
total_sign  = "+" if total_pnl >= 0 else ""
usd_label   = "USD"

# Build the stat chips below the main value
chips = f"""
<div style="display:flex; gap:20px; margin-top:4px; flex-wrap:wrap;">
    <div>
        <span style="font-size:10px; color:#787b86; text-transform:uppercase; letter-spacing:0.07em;">Cash</span><br>
        <span style="font-size:14px; color:#d1d4dc; font-weight:600;">${cash:,.2f}</span>
    </div>
    <div>
        <span style="font-size:10px; color:#787b86; text-transform:uppercase; letter-spacing:0.07em;">Unrealized P&L</span><br>
        <span style="font-size:14px; font-weight:600; color:{pnl_color};">{pnl_sign}${unrealized_pnl:,.2f}</span>
    </div>
    <div>
        <span style="font-size:10px; color:#787b86; text-transform:uppercase; letter-spacing:0.07em;">Today Realized</span><br>
        <span style="font-size:14px; font-weight:600; color:{total_color};">{total_sign}${realized_pnl:,.2f}</span>
    </div>
    <div>
        <span style="font-size:10px; color:#787b86; text-transform:uppercase; letter-spacing:0.07em;">Open Positions</span><br>
        <span style="font-size:14px; color:#d1d4dc; font-weight:600;">{len(positions)}</span>
    </div>
    <div>
        <span style="font-size:10px; color:#787b86; text-transform:uppercase; letter-spacing:0.07em;">Target Progress</span><br>
        <span style="font-size:14px; color:#26a69a; font-weight:600;">{progress_pct:.2f}%</span>
    </div>
</div>
"""

# Hero card header — self-contained, no open divs
st.markdown(f"""
<div style="background:#1e222d; border:1px solid #2a2e39; border-radius:10px 10px 0 0;
            padding:20px 24px 16px 24px;">
    <div style="font-size:11px; color:#787b86; text-transform:uppercase;
                letter-spacing:0.08em; margin-bottom:4px;">Portfolio Value</div>
    <div style="display:flex; align-items:baseline; gap:12px; flex-wrap:wrap;">
        <span style="font-size:42px; font-weight:700; color:#d1d4dc; line-height:1.1;
                     letter-spacing:-1px;">${portfolio_val:,.2f}</span>
        <span style="font-size:14px; color:#787b86; font-weight:400; margin-bottom:4px;">{usd_label}</span>
        <span style="font-size:16px; font-weight:600; color:{total_color};">
            {total_sign}${total_pnl:,.2f} &nbsp;({total_sign}{(total_pnl/portfolio_val*100) if portfolio_val else 0:.2f}%)
        </span>
    </div>
    {chips}
</div>
""", unsafe_allow_html=True)

# Chart sits in its own card panel flush below the header
hero_chart = _portfolio_chart(history, height=260)
if hero_chart:
    st.markdown("""
    <div style="background:#1e222d; border:1px solid #2a2e39; border-top:none;
                border-radius:0 0 10px 10px; padding:0; overflow:hidden; margin-bottom:14px;">
    """, unsafe_allow_html=True)
    st.plotly_chart(hero_chart, width="stretch", config={"displayModeBar": False})
    st.markdown("</div>", unsafe_allow_html=True)
else:
    st.markdown("""
    <div style="background:#1e222d; border:1px solid #2a2e39; border-top:none;
                border-radius:0 0 10px 10px; height:180px; display:flex;
                align-items:center; justify-content:center;
                color:#787b86; font-size:13px; margin-bottom:14px;">
        Chart builds after first end-of-day balance sync
    </div>""", unsafe_allow_html=True)

# ── Progress bar ─────────────────────────────────────────────────────────────
st.markdown(f"""
<div style="background:#1e222d; border:1px solid #2a2e39; border-radius:8px;
            padding:12px 18px; margin-bottom:14px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <span style="font-size:11px; color:#787b86; text-transform:uppercase;
                     letter-spacing:0.08em;">Progress to $1,000,000</span>
        <span style="font-size:12px; color:#26a69a; font-weight:600;">{progress_pct:.2f}% &nbsp;·&nbsp; ${1_000_000-portfolio_val:,.0f} remaining</span>
    </div>
    <div style="background:#2a2e39; border-radius:4px; height:6px; overflow:hidden;">
        <div style="background:linear-gradient(90deg,#26a69a,#00bcd4);
                    width:{progress_pct:.2f}%; height:100%; border-radius:4px;"></div>
    </div>
</div>
""", unsafe_allow_html=True)


# ── Main layout: left (positions) | right (activity + history) ───────────────

left, right = st.columns([2, 1], gap="medium")

with left:

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
            st.plotly_chart(bar, width="stretch", config={"displayModeBar": False})

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
        st.dataframe(styled, width="stretch", hide_index=True, height=min(40 * len(rows) + 38, 300))


with right:
    pass  # right column reserved for future widgets


# ── Activity & Trade History ──────────────────────────────────────────────────

st.markdown(
    '<p style="font-size:11px; font-weight:600; letter-spacing:0.08em; '
    'text-transform:uppercase; color:#787b86; margin:10px 0 6px;">Activity — Last 7 Days</p>',
    unsafe_allow_html=True,
)

# Gather all relevant events from the last 7 days, sorted newest first
recent_all = _load_events(days=7)
history_events = [
    e for e in recent_all
    if e.get("event") in (
        "order_placed", "hard_exit", "ai_close",
        "stop_loss_close", "take_profit_close",
        "daily_loss_halt", "skipped_signal_gate",
        "skipped_momentum", "skipped_liquidity",
        "skipped_earnings", "balance_sync",
    )
]
history_events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

if not history_events:
    st.markdown(
        '<p style="color:#787b86; font-size:13px; padding:4px 0;">No activity in the last 7 days.</p>',
        unsafe_allow_html=True,
    )
else:
    for e in history_events[:50]:
        event  = e.get("event", "")
        ts_raw = e.get("timestamp", "")[:19].replace("T", " ")
        ticker = e.get("ticker") or (e.get("order") or {}).get("ticker", "")
        order  = e.get("order",  {}) or {}
        result = e.get("result", {}) or {}
        pnl    = result.get("realized_pnl")

        # ── Derive display values per event type ──────────────────────────────
        if event == "order_placed":
            badge_text  = "BUY"
            badge_color = "#26a69a"
            badge_bg    = "rgba(38,166,154,0.15)"
            border_col  = "#26a69a"
            amount_str  = f"${order.get('dollar_amount', 0):,.0f}"
            conf_str    = f"Conf {order.get('confidence', 0):.0%}"
            sector_str  = order.get("sector", "")
            detail_line = "  ·  ".join(x for x in [amount_str, conf_str, sector_str] if x)
            reason_str  = order.get("reasoning", "")[:100]
            pnl_str     = ""
            pnl_color   = "#787b86"

        elif event in ("hard_exit", "ai_close", "stop_loss_close", "take_profit_close"):
            badge_text  = "CLOSE"
            badge_color = "#ef5350"
            badge_bg    = "rgba(239,83,80,0.15)"
            border_col  = "#ef5350"
            reason_str  = e.get("reason", event.replace("_", " "))
            pnl_color   = "#26a69a" if pnl and pnl >= 0 else "#ef5350"
            pnl_str     = f'{"+" if pnl and pnl >= 0 else ""}${pnl:,.2f}' if pnl is not None else ""
            detail_line = reason_str[:80]
            reason_str  = ""

        elif event == "daily_loss_halt":
            badge_text  = "HALT"
            badge_color = "#ef5350"
            badge_bg    = "rgba(239,83,80,0.15)"
            border_col  = "#ef5350"
            ticker      = "Circuit Breaker"
            detail_line = f"Daily loss limit hit — new trades paused"
            reason_str  = ""
            pnl_str     = ""
            pnl_color   = "#787b86"

        elif event in ("skipped_signal_gate", "skipped_momentum", "skipped_liquidity", "skipped_earnings"):
            badge_text  = "SKIP"
            badge_color = "#787b86"
            badge_bg    = "rgba(120,123,134,0.15)"
            border_col  = "#2a2e39"
            detail_line = e.get("reason", event.replace("skipped_", "").replace("_", " "))[:80]
            reason_str  = ""
            pnl_str     = ""
            pnl_color   = "#787b86"

        elif event == "balance_sync":
            badge_text  = "SYNC"
            badge_color = "#787b86"
            badge_bg    = "rgba(120,123,134,0.15)"
            border_col  = "#2a2e39"
            ticker      = "Balance Sync"
            detail_line = f"Portfolio updated to ${e.get('portfolio_value', 0):,.2f}"
            reason_str  = ""
            pnl_str     = ""
            pnl_color   = "#787b86"

        else:
            continue

        # ── Render row using native columns (no nested HTML) ──────────────────
        c_badge, c_ticker, c_detail, c_pnl, c_time = st.columns([1, 1.2, 4, 1.5, 1.5])

        with c_badge:
            st.markdown(
                f'<div style="background:{badge_bg}; color:{badge_color}; font-size:10px; '
                f'font-weight:700; padding:4px 8px; border-radius:4px; text-align:center; '
                f'margin-top:2px;">{badge_text}</div>',
                unsafe_allow_html=True,
            )
        with c_ticker:
            st.markdown(f"**{ticker}**")
        with c_detail:
            st.markdown(
                f'<span style="color:#787b86; font-size:12px;">{detail_line}</span>',
                unsafe_allow_html=True,
            )
            if reason_str:
                st.markdown(
                    f'<span style="color:#4b5060; font-size:11px;">{reason_str}</span>',
                    unsafe_allow_html=True,
                )
        with c_pnl:
            if pnl_str:
                st.markdown(
                    f'<span style="color:{pnl_color}; font-size:13px; font-weight:700;">{pnl_str}</span>',
                    unsafe_allow_html=True,
                )
        with c_time:
            st.markdown(
                f'<span style="color:#4b5060; font-size:11px;">{ts_raw[5:]}</span>',
                unsafe_allow_html=True,
            )

        st.markdown('<hr style="margin:2px 0; border-color:#2a2e39;">', unsafe_allow_html=True)


# ── Signal analyses (expandable) ─────────────────────────────────────────────

with st.expander("AI Signal Analyses — Last 24h", expanded=False):
    analyses = [e for e in _load_events(days=1) if e.get("event") == "analysis"]
    if not analyses:
        st.caption("No analyses in the last 24 hours.")
    else:
        for e in reversed(analyses[-40:]):
            d      = e.get("decision", {})
            action = d.get("action", "—")
            conf   = d.get("confidence", 0)
            ticker = e.get("ticker", "—")
            ts     = e.get("timestamp", "")[:19][11:]
            reason = d.get("reasoning", "")[:120]
            risk   = d.get("risk_level", "—")

            a_color = "#26a69a" if action == "BUY" else "#ef5350" if action == "SELL" else "#787b86"
            a_bg    = "rgba(38,166,154,0.15)" if action == "BUY" else "rgba(239,83,80,0.15)" if action == "SELL" else "rgba(120,123,134,0.15)"

            ca, ct, cd, ctime = st.columns([1, 1, 5, 1.5])
            with ca:
                st.markdown(
                    f'<div style="background:{a_bg}; color:{a_color}; font-size:10px; font-weight:700; '
                    f'padding:4px 8px; border-radius:4px; text-align:center; margin-top:2px;">{action}</div>',
                    unsafe_allow_html=True,
                )
            with ct:
                st.markdown(f"**{ticker}**")
            with cd:
                st.markdown(
                    f'<span style="color:#787b86; font-size:12px;">Conf {conf:.0%} · Risk {risk}</span>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f'<span style="color:#4b5060; font-size:11px;">{reason}</span>',
                    unsafe_allow_html=True,
                )
            with ctime:
                st.markdown(
                    f'<span style="color:#4b5060; font-size:11px;">{ts}</span>',
                    unsafe_allow_html=True,
                )
            st.markdown('<hr style="margin:2px 0; border-color:#2a2e39;">', unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style="text-align:center; color:#787b86; font-size:11px; margin-top:16px; padding-top:12px; border-top:1px solid #2a2e39;">
    Congressional Trading Agent &nbsp;·&nbsp;
    DRY_RUN={os.getenv('DRY_RUN','true')} &nbsp;·&nbsp;
    Target $1,000,000 &nbsp;·&nbsp;
    Data: Alpaca Markets
</div>
""", unsafe_allow_html=True)
