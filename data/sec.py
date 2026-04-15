"""
sec.py
Fetches recent SEC EDGAR filings for a given ticker from sec-api.io.
Focuses on 8-K (material events), 10-Q (quarterly reports), and Form 4 (insider trades).
Requires SEC_API_KEY in .env — free tier: 100 requests/month.
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from utils import fetch_with_retry

load_dotenv()

SEC_API_KEY  = os.getenv("SEC_API_KEY")
SEC_API_BASE = "https://api.sec-api.io"


def _post_json(url: str, payload: dict, headers: dict) -> dict:
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_sec_filings(ticker: str, days_back: int = 14, forms: str = "8-K,10-Q,4") -> list[dict]:
    """
    Query recent SEC filings for a ticker using the sec-api.io full-text search endpoint.
    Returns a list of filing summaries sorted newest first.

    forms: comma-separated list of form types to include.
    """
    if not SEC_API_KEY or SEC_API_KEY == "your_sec_api_key_here":
        print("[sec] SEC API key not set — skipping.")
        return []

    start_dt = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_dt   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "query": {
            "query_string": {
                "query": f'ticker:{ticker} AND formType:({" OR ".join(forms.split(","))})'
            }
        },
        "dateRange": {
            "startdt": start_dt,
            "enddt":   end_dt,
        },
        "from":  "0",
        "size":  "5",
        "sort":  [{"filedAt": {"order": "desc"}}],
    }

    headers = {"Authorization": SEC_API_KEY}

    try:
        data = fetch_with_retry(lambda: _post_json(SEC_API_BASE, payload, headers))
    except Exception as e:
        print(f"[sec] SEC API error for {ticker}: {e}")
        return []

    results = []
    for filing in data.get("filings", []):
        results.append({
            "ticker":       ticker,
            "form_type":    filing.get("formType", ""),
            "filed_at":     filing.get("filedAt", ""),
            "company_name": filing.get("companyName", ""),
            "description":  filing.get("description", ""),
            "url":          filing.get("linkToFilingDetails", ""),
        })

    print(f"[sec] {len(results)} filing(s) found for {ticker} (last {days_back} days)")
    return results


def summarise_sec_filings(filings: list[dict]) -> str:
    """Format SEC filings into a readable string for the Claude prompt."""
    if not filings:
        return "No recent SEC filings found."

    lines = []
    for f in filings:
        desc = f.get("description") or "(no description)"
        lines.append(
            f"- [{f['form_type']}] {f['company_name']} — {desc} (filed {f['filed_at'][:10]})"
        )
    return "\n".join(lines)
