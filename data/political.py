"""
political.py
Fetches congressional stock disclosures from:
  - House Stock Watcher (free, no key)
  - Senate Stock Watcher (free, no key)
  - Quiver Quantitative (requires API key)
"""

import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

from utils import fetch_with_retry

load_dotenv()

QUIVER_API_KEY = os.getenv("QUIVER_API_KEY")
QUIVER_BASE    = "https://api.quiverquant.com/beta"

HOUSE_URL  = "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"
SENATE_URL = "https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com/aggregate/all_transactions.json"


def _days_ago(n: int) -> datetime:
    return datetime.utcnow() - timedelta(days=n)


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
    """Merge trades from all sources, deduplicate, and sort newest first."""
    trades = get_house_trades(days_back) + get_senate_trades(days_back) + get_quiver_trades(days_back)

    seen   = set()
    unique = []
    for t in trades:
        key = (t["member"], t["ticker"], t["transaction_date"])
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda x: x["disclosure_date"], reverse=True)
    print(f"[political] Found {len(unique)} unique trades (last {days_back} days)")
    return unique


# ── Summary helper used by Claude prompt ────────────────────────────────────

def summarise_trades(trades: list[dict]) -> str:
    """Format trade list into a readable string for the AI prompt."""
    if not trades:
        return "No recent congressional disclosures found."
    lines = []
    for t in trades:
        direction = "BOUGHT" if "purchase" in t["transaction_type"] else "SOLD"
        lines.append(
            f"- {t['member']} ({t['source'].upper()}) {direction} {t['ticker']} "
            f"({t['amount_range']}) on {t['transaction_date']} "
            f"[disclosed {t['disclosure_date']}]"
        )
    return "\n".join(lines)
