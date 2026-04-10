"""
broker.py
Alpaca Markets integration — order placement, portfolio queries, position management.
Switch ALPACA_BASE_URL in .env from paper → live when ready for real money.
"""

import os
import alpaca_trade_api as tradeapi
from dotenv import load_dotenv

load_dotenv()

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

_api: tradeapi.REST | None = None


def _get_api() -> tradeapi.REST:
    global _api
    if _api is None:
        key    = os.getenv("ALPACA_API_KEY", "")
        secret = os.getenv("ALPACA_SECRET_KEY", "")
        url    = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

        if not key or key == "your_alpaca_api_key_here":
            raise EnvironmentError("ALPACA_API_KEY is not set in .env")
        if not secret or secret == "your_alpaca_secret_key_here":
            raise EnvironmentError("ALPACA_SECRET_KEY is not set in .env")

        _api = tradeapi.REST(key_id=key, secret_key=secret, base_url=url)
    return _api


# ── Account / Portfolio ───────────────────────────────────────────────────────

def get_account() -> dict:
    api = _get_api()
    acct = api.get_account()
    return {
        "portfolio_value": float(acct.portfolio_value),
        "cash":            float(acct.cash),
        "buying_power":    float(acct.buying_power),
        "equity":          float(acct.equity),
    }


def get_portfolio_value() -> float:
    return get_account()["portfolio_value"]


def get_open_positions() -> list[dict]:
    """Return list of open positions with key fields."""
    api = _get_api()
    positions = api.list_positions()
    result = []
    for p in positions:
        result.append({
            "symbol":              p.symbol,
            "qty":                 float(p.qty),
            "avg_entry_price":     float(p.avg_entry_price),
            "current_price":       float(p.current_price),
            "market_value":        float(p.market_value),
            "unrealized_pl":       float(p.unrealized_pl),
            "unrealized_plpc":     float(p.unrealized_plpc),
        })
    return result


def get_open_tickers() -> list[str]:
    return [p["symbol"] for p in get_open_positions()]


def portfolio_snapshot_text() -> str:
    """Human-readable portfolio summary for the Claude prompt."""
    try:
        acct = get_account()
        positions = get_open_positions()
        lines = [
            f"Portfolio value: ${acct['portfolio_value']:,.2f}",
            f"Cash available:  ${acct['cash']:,.2f}",
            f"Open positions ({len(positions)}):",
        ]
        for p in positions:
            lines.append(
                f"  {p['symbol']}: {p['qty']} shares, "
                f"P&L {p['unrealized_plpc']:+.1%} (${p['unrealized_pl']:+.2f})"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Portfolio unavailable: {e}"


# ── Order Placement ───────────────────────────────────────────────────────────

def place_order(order: dict) -> dict | None:
    """
    Submit a market order.
    Uses fractional/notional ordering so any dollar amount works.

    order keys: ticker, action, dollar_amount
    """
    ticker        = order["ticker"]
    action        = order["action"].lower()   # "buy" or "sell"
    dollar_amount = order["dollar_amount"]

    if DRY_RUN:
        print(f"[broker] DRY RUN — would {action.upper()} ${dollar_amount:.2f} of {ticker}")
        return {"dry_run": True, "ticker": ticker, "action": action, "amount": dollar_amount}

    try:
        api = _get_api()
        submitted = api.submit_order(
            symbol=ticker,
            notional=dollar_amount,
            side=action,
            type="market",
            time_in_force="day",
        )
        print(f"[broker] Order submitted: {action.upper()} ${dollar_amount:.2f} of {ticker} — id={submitted.id}")
        return {
            "order_id":  submitted.id,
            "ticker":    ticker,
            "action":    action,
            "amount":    dollar_amount,
            "status":    submitted.status,
        }
    except Exception as e:
        print(f"[broker] Order failed for {ticker}: {e}")
        return None


def close_position(ticker: str) -> dict | None:
    """Close (liquidate) an entire position."""
    if DRY_RUN:
        print(f"[broker] DRY RUN — would close position in {ticker}")
        return {"dry_run": True, "ticker": ticker, "action": "close"}

    try:
        api = _get_api()
        result = api.close_position(ticker)
        print(f"[broker] Closed position in {ticker}")
        return {"ticker": ticker, "action": "close", "order_id": result.id}
    except Exception as e:
        print(f"[broker] Failed to close {ticker}: {e}")
        return None


def is_market_open() -> bool:
    try:
        clock = _get_api().get_clock()
        return clock.is_open
    except Exception:
        return False
