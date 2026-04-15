"""
market.py
Market regime detection, per-ticker volatility, and earnings calendar checks.
Uses Alpaca data API (same credentials as broker) + yfinance for earnings/sector.
"""

import math
import os
import statistics
import requests
from datetime import datetime, timedelta, timezone, date
from dotenv import load_dotenv

load_dotenv()

ALPACA_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
ALPACA_DATA   = "https://data.alpaca.markets"

EARNINGS_BUFFER_DAYS = int(os.getenv("EARNINGS_BUFFER_DAYS", 5))
TARGET_ANNUAL_VOL    = 0.20   # 20% — benchmark for position sizing


def _headers() -> dict:
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }


def _get_daily_closes(ticker: str, days: int) -> list[float]:
    """Fetch daily close prices from Alpaca data API."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days + 15)   # buffer for weekends/holidays
    try:
        resp = requests.get(
            f"{ALPACA_DATA}/v2/stocks/{ticker}/bars",
            headers=_headers(),
            params={
                "timeframe": "1Day",
                "start":     start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end":       end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit":     days + 15,
                "feed":      "iex",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return [b["c"] for b in resp.json().get("bars", [])]
    except Exception:
        return []


# ── Market Regime ─────────────────────────────────────────────────────────────

def get_market_regime() -> str:
    """
    Returns 'BULL' or 'BEAR' based on whether SPY is above/below its 50-day MA.
    In BEAR mode the agent raises MIN_CONFIDENCE by 0.10 for new BUY trades.
    """
    closes = _get_daily_closes("SPY", 65)
    if len(closes) < 10:
        print("[market] Could not determine regime — defaulting to BULL")
        return "BULL"
    ma50    = sum(closes[-50:]) / min(50, len(closes))
    current = closes[-1]
    regime  = "BULL" if current > ma50 else "BEAR"
    print(f"[market] SPY ${current:.2f} vs 50-day MA ${ma50:.2f} → {regime}")
    return regime


# ── Volatility ────────────────────────────────────────────────────────────────

def get_volatility(ticker: str) -> float:
    """
    Returns annualized volatility using 20-day log returns.
    Position sizing scales inversely: high-vol tickers get smaller positions.
    Defaults to TARGET_ANNUAL_VOL (0.20) if data is unavailable.
    """
    closes = _get_daily_closes(ticker, 35)
    if len(closes) < 5:
        return TARGET_ANNUAL_VOL
    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(max(1, len(closes) - 20), len(closes))
    ]
    if len(log_returns) < 3:
        return TARGET_ANNUAL_VOL
    daily_vol  = statistics.stdev(log_returns)
    annual_vol = daily_vol * math.sqrt(252)
    return max(annual_vol, 0.05)   # floor at 5% to avoid division edge cases


def vol_size_scalar(ticker_vol: float) -> float:
    """
    Returns a multiplier (0.25–1.0) to scale position size by volatility.
    Low-vol stock (10% annual) → scalar 1.0 (full size)
    At-target stock (20% annual) → scalar 1.0
    High-vol stock (40% annual) → scalar 0.50
    Very high-vol (80% annual) → scalar 0.25 (minimum)
    """
    scalar = TARGET_ANNUAL_VOL / ticker_vol
    return max(0.25, min(1.0, scalar))


# ── Earnings Calendar ─────────────────────────────────────────────────────────

def get_next_earnings_date(ticker: str) -> date | None:
    """Returns the next earnings date for a ticker, or None if unavailable."""
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
        # yfinance ≥0.2: returns a dict
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
        else:
            # older versions returned a DataFrame
            try:
                dates = list(cal.loc["Earnings Date"])
            except Exception:
                return None
        if not dates:
            return None
        for d in dates:
            if hasattr(d, "date"):
                return d.date()
            if isinstance(d, date):
                return d
        return None
    except ImportError:
        return None
    except Exception as e:
        print(f"[market] Earnings lookup failed for {ticker}: {e}")
        return None


def earnings_too_close(ticker: str) -> bool:
    """
    Returns True if the next earnings date is within EARNINGS_BUFFER_DAYS.
    Prevents buying into binary earnings events.
    """
    next_date = get_next_earnings_date(ticker)
    if next_date is None:
        return False
    days_away = (next_date - datetime.now(timezone.utc).date()).days
    if 0 <= days_away <= EARNINGS_BUFFER_DAYS:
        print(f"[market] {ticker} earnings in {days_away}d ({next_date}) — skipping to avoid binary event.")
        return True
    return False
