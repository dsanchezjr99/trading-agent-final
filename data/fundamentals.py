"""
fundamentals.py
Extended yfinance data: short interest, institutional ownership, insider transactions.
No API key required.

Short interest context: high short interest + congressional buying = potential squeeze setup.
Institutional ownership: smart money positioning relative to the congressional signal.
Insider transactions: company executives buying their own stock amplifies the signal.
"""

from datetime import datetime, timezone

import yfinance as yf


def get_fundamentals(ticker: str) -> dict:
    """
    Fetch short interest, institutional ownership %, and recent insider transactions.
    Returns an empty dict on failure — callers must handle gracefully.
    """
    try:
        stock = yf.Ticker(ticker)
        info  = stock.info

        short_pct = info.get("shortPercentOfFloat")
        inst_pct  = info.get("institutionsPercentHeld")

        insider_txns = _get_insider_transactions(stock)

        return {
            "short_interest_pct":       short_pct,
            "institutional_ownership_pct": inst_pct,
            "insider_transactions":     insider_txns,
        }
    except Exception as e:
        print(f"[fundamentals] yfinance extended data failed for {ticker}: {e}")
        return {}


def _get_insider_transactions(stock) -> list[dict]:
    """Parse insider_transactions DataFrame into a clean list."""
    try:
        df = stock.insider_transactions
        if df is None or df.empty:
            return []

        results = []
        for _, row in df.head(5).iterrows():
            shares = row.get("Shares", 0)
            try:
                shares = int(shares) if shares and str(shares) != "nan" else 0
            except (ValueError, TypeError):
                shares = 0

            value = row.get("Value", None)
            try:
                value = float(value) if value and str(value) != "nan" else None
            except (ValueError, TypeError):
                value = None

            date = row.get("Start Date", "")
            if hasattr(date, "strftime"):
                date = date.strftime("%Y-%m-%d")
            elif isinstance(date, str):
                date = date[:10]

            results.append({
                "insider":     str(row.get("Insider", "Unknown")).strip(),
                "position":    str(row.get("Position", "")).strip(),
                "transaction": str(row.get("Transaction", "")).strip(),
                "shares":      shares,
                "value":       value,
                "date":        date,
                "ownership":   str(row.get("Ownership", "")).strip(),  # D=direct, I=indirect
            })
        return results
    except Exception:
        return []


def summarise_fundamentals(data: dict) -> str:
    if not data:
        return "No fundamental data available."

    lines = []

    short_pct = data.get("short_interest_pct")
    if short_pct is not None:
        level = "HIGH — potential squeeze setup" if short_pct > 0.15 else \
                "MODERATE" if short_pct > 0.05 else "LOW"
        lines.append(f"Short interest: {short_pct:.1%} of float [{level}]")

    inst_pct = data.get("institutional_ownership_pct")
    if inst_pct is not None:
        lines.append(f"Institutional ownership: {inst_pct:.1%}")

    insider_txns = data.get("insider_transactions", [])
    buys  = [t for t in insider_txns if t["shares"] > 0]
    sells = [t for t in insider_txns if t["shares"] < 0]

    if insider_txns:
        lines.append(f"Recent insider activity ({len(buys)} buy(s), {len(sells)} sell(s) in last 5 transactions):")
        for t in insider_txns[:4]:
            direction = "BUY" if t["shares"] > 0 else "SELL" if t["shares"] < 0 else t["transaction"]
            value_str = f" (${t['value']:,.0f})" if t.get("value") else ""
            lines.append(
                f"  - {t['date']}: {t['insider']} [{t['position']}] {direction} "
                f"{abs(t['shares']):,} shares{value_str}"
            )

    return "\n".join(lines) if lines else "No fundamental data available."
