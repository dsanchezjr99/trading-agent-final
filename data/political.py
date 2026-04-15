"""
political.py
Fetches congressional stock disclosures from:
  - House Stock Watcher (free, no key)
  - Senate Stock Watcher (free, no key)
  - Quiver Quantitative (requires API key)
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from utils import fetch_with_retry

load_dotenv()

QUIVER_API_KEY = os.getenv("QUIVER_API_KEY")
QUIVER_BASE    = "https://api.quiverquant.com/beta"

HOUSE_URL  = "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"
SENATE_URL = "https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com/aggregate/all_transactions.json"

# Only evaluate trades where the stated minimum dollar amount is at or above this threshold.
# Removes the $1k–$15k small trades that are portfolio noise, not conviction signals.
MIN_TRADE_AMOUNT = int(os.getenv("MIN_TRADE_AMOUNT", 15000))

# Only consider trades whose TRANSACTION date (not disclosure date) is within this window.
# Avoids acting on signals that are already 6 weeks old and fully priced in.
MAX_TRANSACTION_AGE_DAYS = int(os.getenv("MAX_TRANSACTION_AGE_DAYS", 30))


def _days_ago(n: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)


def _parse_min_amount(amount_str: str) -> int:
    """
    Extracts the lower bound dollar value from Alpaca amount range strings.
    Examples:
      '$1,001 - $15,000'  → 1001
      '$50,001 - $100,000' → 50001
      'Over $1,000,000'   → 1000000
      ''                  → 0
    """
    if not amount_str:
        return 0
    clean = amount_str.replace(",", "").replace("$", "").strip()
    if clean.lower().startswith("over"):
        try:
            return int(clean.lower().replace("over", "").strip())
        except ValueError:
            return 0
    parts = clean.split("-")
    try:
        return int(parts[0].strip())
    except ValueError:
        return 0


def _parse_date(date_str: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(date_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _fetch_json(url: str, **kwargs) -> list | dict:
    resp = requests.get(url, **kwargs)
    resp.raise_for_status()
    return resp.json()


# ── House Stock Watcher ──────────────────────────────────────────────────────

def get_house_trades(days_back: int = 7) -> list[dict]:
    """Return House member trades filed within the last `days_back` days."""
    try:
        trades = fetch_with_retry(lambda: _fetch_json(HOUSE_URL, timeout=15))
    except Exception as e:
        print(f"[political] House fetch error: {e}")
        return []

    cutoff  = _days_ago(days_back)
    results = []

    for t in trades:
        filed = _parse_date(t.get("disclosure_date") or t.get("transaction_date", ""))
        if not filed or filed < cutoff:
            continue
        ticker = t.get("ticker", "").strip().upper()
        if not ticker or ticker in ("--", "N/A", ""):
            continue
        results.append({
            "source":           "house",
            "member":           t.get("representative", "Unknown"),
            "ticker":           ticker,
            "asset_description": t.get("asset_description", ""),
            "transaction_type": t.get("type", "").lower(),
            "amount_range":     t.get("amount", ""),
            "transaction_date": t.get("transaction_date", ""),
            "disclosure_date":  t.get("disclosure_date", ""),
        })

    return results


# ── Senate Stock Watcher ─────────────────────────────────────────────────────

def get_senate_trades(days_back: int = 7) -> list[dict]:
    """Return Senate member trades filed within the last `days_back` days."""
    try:
        trades = fetch_with_retry(lambda: _fetch_json(SENATE_URL, timeout=15))
    except Exception as e:
        print(f"[political] Senate fetch error: {e}")
        return []

    cutoff  = _days_ago(days_back)
    results = []

    for t in trades:
        filed = _parse_date(t.get("disclosure_date") or t.get("transaction_date", ""))
        if not filed or filed < cutoff:
            continue
        ticker = t.get("ticker", "").strip().upper()
        if not ticker or ticker in ("--", "N/A", ""):
            continue
        results.append({
            "source":           "senate",
            "member":           t.get("senator", "Unknown"),
            "ticker":           ticker,
            "asset_description": t.get("asset_description", ""),
            "transaction_type": t.get("type", "").lower(),
            "amount_range":     t.get("amount", ""),
            "transaction_date": t.get("transaction_date", ""),
            "disclosure_date":  t.get("disclosure_date", ""),
        })

    return results


# ── Quiver Quantitative ──────────────────────────────────────────────────────

def get_quiver_trades(days_back: int = 7) -> list[dict]:
    """
    Fetch congressional trades from Quiver Quant API.
    Requires QUIVER_API_KEY in .env
    """
    if not QUIVER_API_KEY or QUIVER_API_KEY == "your_quiver_api_key_here":
        print("[political] Quiver API key not set — skipping.")
        return []

    headers = {"Authorization": f"Token {QUIVER_API_KEY}"}
    cutoff  = _days_ago(days_back).strftime("%Y-%m-%d")

    try:
        raw = fetch_with_retry(lambda: _fetch_json(
            f"{QUIVER_BASE}/live/congresstrading",
            headers=headers,
            params={"startDate": cutoff},
            timeout=15,
        ))
    except Exception as e:
        print(f"[political] Quiver fetch error: {e}")
        return []

    results = []
    for t in raw:
        ticker = t.get("Ticker", "").strip().upper()
        if not ticker:
            continue
        results.append({
            "source":           "quiver",
            "member":           t.get("Representative", "Unknown"),
            "ticker":           ticker,
            "asset_description": t.get("Asset", ""),
            "transaction_type": t.get("Transaction", "").lower(),
            "amount_range":     t.get("Range", ""),
            "transaction_date": t.get("TransactionDate", ""),
            "disclosure_date":  t.get("DisclosureDate", ""),
        })

    return results


# ── Combined entry point ─────────────────────────────────────────────────────

def get_all_political_trades(days_back: int = 7) -> list[dict]:
    """
    Merge trades from all sources, deduplicate, and apply quality filters:
      1. Transaction date must be within MAX_TRANSACTION_AGE_DAYS (avoids stale signals)
      2. Trade amount lower bound must be >= MIN_TRADE_AMOUNT (removes noise trades)
    """
    trades = get_house_trades(days_back) + get_senate_trades(days_back) + get_quiver_trades(days_back)

    transaction_cutoff = _days_ago(MAX_TRANSACTION_AGE_DAYS)

    seen    = set()
    unique  = []
    removed = 0
    for t in trades:
        key = (t["member"], t["ticker"], t["transaction_date"])
        if key in seen:
            continue
        seen.add(key)

        # Filter 1: transaction must be recent enough to be actionable
        tx_date = _parse_date(t["transaction_date"])
        # _parse_date returns naive datetimes; strip tzinfo from cutoff for comparison
        if tx_date and tx_date < transaction_cutoff.replace(tzinfo=None):
            removed += 1
            continue

        # Filter 2: minimum trade size — skip $1k–$15k noise
        if _parse_min_amount(t["amount_range"]) < MIN_TRADE_AMOUNT:
            removed += 1
            continue

        unique.append(t)

    unique.sort(key=lambda x: x["disclosure_date"], reverse=True)
    print(f"[political] Found {len(unique)} qualifying trades (filtered {removed} below threshold)")
    return unique


# ── Summary helper used by Claude prompt ────────────────────────────────────

def passes_signal_gate(trades: list[dict]) -> tuple[bool, str]:
    """
    Option B — Rules-based signal gate.
    Requires ALL of the following before passing to Claude:
      1. At least 2 unique buyers
      2. Buyers outnumber sellers (net bullish)
      3. Aggregate disclosed minimum amount >= $50,000

    Returns (passed, reason).
    """
    buyers  = [t for t in trades if "purchase" in t.get("transaction_type", "").lower()]
    sellers = [t for t in trades if "sale" in t.get("transaction_type", "").lower()
               and "purchase" not in t.get("transaction_type", "").lower()]

    unique_buyers = len({t["member"] for t in buyers})

    aggregate_min = sum(_parse_min_amount(t.get("amount_range", "")) for t in buyers)

    if unique_buyers < 2:
        return False, f"Signal gate: only {unique_buyers} unique buyer(s) — need ≥2"

    if len(sellers) >= len(buyers):
        return False, f"Signal gate: {len(sellers)} seller(s) vs {len(buyers)} buyer(s) — not net bullish"

    if aggregate_min < 50_000:
        return False, f"Signal gate: aggregate buy amount ${aggregate_min:,} below $50,000 threshold"

    return True, f"Signal gate passed: {unique_buyers} buyers, ${aggregate_min:,} aggregate, {len(sellers)} sellers"


def _signal_strength(transaction_date: str, max_days: int = 30) -> str:
    """
    Returns a signal strength label based on how old the transaction is.
    Newer disclosures carry more alpha — older ones are likely priced in.
      0–7 days:   STRONG
      8–14 days:  MODERATE
      15–30 days: WEAK
    """
    parsed = _parse_date(transaction_date)
    if not parsed:
        return "UNKNOWN"
    days_old = (datetime.now(timezone.utc).replace(tzinfo=None) - parsed).days
    if days_old <= 7:
        return "STRONG"
    if days_old <= 14:
        return "MODERATE"
    return "WEAK"


def summarise_trades(trades: list[dict]) -> str:
    """
    Format trade list into a readable string for the AI prompt.
    Includes signal strength decay label so Claude weights recent trades higher.
    """
    if not trades:
        return "No recent congressional disclosures found."
    lines = []
    for t in trades:
        direction = "BOUGHT" if "purchase" in t["transaction_type"] else "SOLD"
        strength  = _signal_strength(t["transaction_date"])
        lines.append(
            f"- [{strength}] {t['member']} ({t['source'].upper()}) {direction} {t['ticker']} "
            f"({t['amount_range']}) on {t['transaction_date']} "
            f"[disclosed {t['disclosure_date']}]"
        )
    return "\n".join(lines)
