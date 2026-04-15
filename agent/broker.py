"""
broker.py
Alpaca Markets integration — order placement, portfolio queries, position management.
Switch ALPACA_BASE_URL in .env from paper → live when ready for real money.
"""

import os
import requests
import alpaca_trade_api as tradeapi
from dotenv import load_dotenv

load_dotenv()

DRY_RUN       = os.getenv("DRY_RUN", "true").lower() == "true"
ALPACA_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_DATA   = "https://data.alpaca.markets"

_api: tradeapi.REST | None = None


def _get_api() -> tradeapi.REST:
    global _api
    if _api is None:
        url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        if not ALPACA_KEY or ALPACA_KEY == "your_alpaca_api_key_here":
            raise EnvironmentError("ALPACA_API_KEY is not set in .env")
        if not ALPACA_SECRET or ALPACA_SECRET == "your_alpaca_secret_key_here":
            raise EnvironmentError("ALPACA_SECRET_KEY is not set in .env")
        _api = tradeapi.REST(key_id=ALPACA_KEY, secret_key=ALPACA_SECRET, base_url=url)
    return _api


def _data_headers() -> dict:
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }


# ── Account / Portfolio ───────────────────────────────────────────────────────

def get_account() -> dict:
    api  = _get_api()
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
    api       = _get_api()
    positions = api.list_positions()
    return [
        {
            "symbol":          p.symbol,
            "qty":             float(p.qty),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price":   float(p.current_price),
            "market_value":    float(p.market_value),
            "unrealized_pl":   float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
        }
        for p in positions
    ]


def get_current_price(ticker: str) -> float | None:
    """
    Fetch the latest close price for a ticker from the Alpaca data API.
    Used to calculate limit order prices.
    """
    try:
        resp = requests.get(
            f"{ALPACA_DATA}/v2/stocks/{ticker}/bars/latest",
            headers=_data_headers(),
            params={"feed": "iex"},
            timeout=10,
        )
        resp.raise_for_status()
        bar = resp.json().get("bar", {})
        return float(bar.get("c", 0)) or None
    except Exception as e:
        print(f"[broker] Could not fetch price for {ticker}: {e}")
        return None


def portfolio_snapshot_text() -> str:
    """Human-readable portfolio summary for the Claude prompt."""
    try:
        acct      = get_account()
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


def is_market_open() -> bool:
    try:
        return _get_api().get_clock().is_open
    except Exception:
        return False


# ── Order Placement ───────────────────────────────────────────────────────────

def place_order(order: dict) -> dict | None:
    """
    Submit a limit order with a 0.2% buffer above/below current price.
    Falls back to market order if current price is unavailable.

    order keys: ticker, action, dollar_amount
    """
    ticker        = order["ticker"]
    action        = order["action"].lower()
    dollar_amount = order["dollar_amount"]

    if DRY_RUN:
        print(f"[broker] DRY RUN — would {action.upper()} ${dollar_amount:.2f} of {ticker}")
        return {"dry_run": True, "ticker": ticker, "action": action, "amount": dollar_amount}

    api = _get_api()

    # Try limit order first
    price = get_current_price(ticker)
    if price and price > 0:
        limit_price = round(price * 1.002 if action == "buy" else price * 0.998, 2)
        qty         = round(dollar_amount / limit_price, 4)
        try:
            submitted = api.submit_order(
                symbol=ticker,
                qty=str(qty),
                side=action,
                type="limit",
                limit_price=str(limit_price),
                time_in_force="day",
            )
            print(
                f"[broker] Limit order: {action.upper()} {qty} {ticker} "
                f"@ ${limit_price:.2f} (≈${dollar_amount:.2f}) — id={submitted.id}"
            )
            return {
                "order_id":    submitted.id,
                "ticker":      ticker,
                "action":      action,
                "amount":      dollar_amount,
                "qty":         qty,
                "limit_price": limit_price,
                "status":      submitted.status,
                "order_type":  "limit",
            }
        except Exception as e:
            print(f"[broker] Limit order failed for {ticker}: {e} — falling back to market order")

    # Fallback: market order (notional)
    try:
        submitted = api.submit_order(
            symbol=ticker,
            notional=dollar_amount,
            side=action,
            type="market",
            time_in_force="day",
        )
        print(f"[broker] Market order: {action.upper()} ${dollar_amount:.2f} of {ticker} — id={submitted.id}")
        return {
            "order_id":   submitted.id,
            "ticker":     ticker,
            "action":     action,
            "amount":     dollar_amount,
            "status":     submitted.status,
            "order_type": "market",
        }
    except Exception as e:
        print(f"[broker] Order failed for {ticker}: {e}")
        return None


def close_position(ticker: str, position: dict | None = None) -> dict | None:
    """
    Liquidate an entire position.
    Pass the position dict to include realized P&L in the return value.
    """
    realized_pnl = round(float(position.get("unrealized_pl",   0)), 2) if position else None
    realized_pct = round(float(position.get("unrealized_plpc", 0)), 4) if position else None

    if DRY_RUN:
        print(f"[broker] DRY RUN — would close {ticker}"
              + (f" (P&L ${realized_pnl:+.2f} / {realized_pct:+.1%})" if realized_pnl is not None else ""))
        return {
            "dry_run":       True,
            "ticker":        ticker,
            "action":        "close",
            "realized_pnl":  realized_pnl,
            "realized_pct":  realized_pct,
        }

    try:
        result = _get_api().close_position(ticker)
        print(f"[broker] Closed {ticker}"
              + (f" (P&L ${realized_pnl:+.2f} / {realized_pct:+.1%})" if realized_pnl is not None else ""))
        return {
            "order_id":     result.id,
            "ticker":       ticker,
            "action":       "close",
            "realized_pnl": realized_pnl,
            "realized_pct": realized_pct,
        }
    except Exception as e:
        print(f"[broker] Failed to close {ticker}: {e}")
        return None
