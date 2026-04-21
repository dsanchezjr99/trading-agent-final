"""
sec.py
Fetches recent SEC EDGAR filings using the official EDGAR REST API.
No API key required. No monthly request cap.
Replaces sec-api.io (which had a 100 req/month free tier limit).

Endpoints used:
  https://www.sec.gov/files/company_tickers.json  — ticker → CIK map
  https://data.sec.gov/submissions/CIK{cik}.json  — recent filings per company
"""

import time
import requests
from datetime import datetime, timedelta, timezone

EDGAR_BASE   = "https://data.sec.gov"
TICKERS_URL  = "https://www.sec.gov/files/company_tickers.json"

# SEC requires a descriptive User-Agent — use app name + contact email
_HEADERS = {"User-Agent": "trading-agent dsanchezjr99@gmail.com"}

# ── CIK map (loaded once, cached for process lifetime) ────────────────────────
_ticker_cik:  dict[str, str] = {}
_cik_loaded:  bool           = False


def _load_cik_map() -> None:
    global _cik_loaded, _ticker_cik
    if _cik_loaded:
        return
    _cik_loaded = True  # set early so a failure doesn't loop
    try:
        resp = requests.get(TICKERS_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        for entry in resp.json().values():
            ticker = entry.get("ticker", "").upper()
            cik    = str(entry.get("cik_str", "")).zfill(10)
            if ticker:
                _ticker_cik[ticker] = cik
        print(f"[sec] Loaded {len(_ticker_cik)} ticker→CIK mappings from EDGAR")
    except Exception as e:
        print(f"[sec] CIK map load failed: {e}")


# ── Main fetch ─────────────────────────────────────────────────────────────────

TARGET_FORMS = {"8-K", "10-Q", "4"}


def get_sec_filings(
    ticker: str,
    days_back: int = 14,
    forms: set = TARGET_FORMS,
    limit: int = 5,
) -> list[dict]:
    """
    Returns recent SEC filings for a ticker filtered by form type and date.
    Uses the official EDGAR submissions API — no key, no cap.

    Form types:
      8-K  — material events (earnings misses, M&A, regulatory actions)
      10-Q — quarterly report
      4    — insider transactions (company executives buying/selling)
    """
    _load_cik_map()

    cik = _ticker_cik.get(ticker.upper())
    if not cik:
        print(f"[sec] No CIK found for {ticker} — skipping SEC lookup")
        return []

    try:
        time.sleep(0.11)   # SEC rate limit: max 10 req/sec
        resp = requests.get(
            f"{EDGAR_BASE}/submissions/CIK{cik}.json",
            headers=_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[sec] EDGAR submissions fetch failed for {ticker}: {e}")
        return []

    recent       = data.get("filings", {}).get("recent", {})
    dates        = recent.get("filingDate", [])
    form_types   = recent.get("form", [])
    items        = recent.get("items", [])
    accessions   = recent.get("accessionNumber", [])
    doc_descs    = recent.get("primaryDocDescription", [])
    company_name = data.get("name", ticker)

    cutoff  = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    results = []

    for date, form, item, acc, doc_desc in zip(dates, form_types, items, accessions, doc_descs):
        if date < cutoff:
            break  # submissions are newest-first — stop once past the window
        if form not in forms:
            continue

        # Build description: prefer items field (e.g. "1.01 Entry into agreement"),
        # fall back to doc description, then form type label
        description = (
            item.strip() if item.strip()
            else doc_desc.strip() if doc_desc.strip()
            else _form_label(form)
        )

        acc_nodash = acc.replace("-", "")
        url = (
            f"https://www.sec.gov/Archives/edgar/data"
            f"/{int(cik)}/{acc_nodash}/{acc}.txt"
        )

        results.append({
            "ticker":       ticker,
            "form_type":    form,
            "filed_at":     date,
            "company_name": company_name,
            "description":  description,
            "url":          url,
        })

        if len(results) >= limit:
            break

    print(f"[sec] {len(results)} filing(s) found for {ticker} (last {days_back} days, EDGAR direct)")
    return results


def _form_label(form: str) -> str:
    return {
        "8-K":  "Material event disclosure",
        "10-Q": "Quarterly report",
        "4":    "Insider transaction",
    }.get(form, form)


# ── Summary helper used by Claude prompt ─────────────────────────────────────

def summarise_sec_filings(filings: list[dict]) -> str:
    if not filings:
        return "No recent SEC filings found."
    lines = []
    for f in filings:
        lines.append(
            f"- [{f['form_type']}] {f['company_name']} — {f['description']} (filed {f['filed_at']})"
        )
    return "\n".join(lines)
