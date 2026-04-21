"""
contracts.py
Fetches recent federal contract awards from USASpending.gov.
No API key required.

Most relevant for: Defense, Energy, Health Care, and Industrials stocks.
When a committee member buys a stock shortly before a major contract is awarded
to that company, it confirms the thesis — they knew it was coming.
"""

import requests
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import yfinance as yf

USASPENDING_BASE = "https://api.usaspending.gov/api/v2"

# Contract type codes: A=BPA Call, B=Purchase Order, C=Delivery Order, D=Definitive Contract
CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]

# Only surface contracts from agencies where congressional oversight creates signal
PRIORITY_AGENCIES = {
    "Department of Defense",
    "Department of Energy",
    "Department of Health and Human Services",
    "Department of Homeland Security",
    "National Aeronautics and Space Administration",
    "Department of Veterans Affairs",
}


@lru_cache(maxsize=128)
def _company_name(ticker: str) -> str:
    """Resolve ticker to full company name for USASpending keyword search."""
    try:
        info = yf.Ticker(ticker).info
        return info.get("longName") or info.get("shortName") or ticker
    except Exception:
        return ticker


def get_contract_awards(
    ticker: str,
    days_back: int = 90,
    limit: int = 5,
) -> list[dict]:
    """
    Returns recent federal contract awards for the company behind a ticker.
    Searches by company name against USASpending.gov's full contract database.

    days_back: look-back window for award date (default 90 days)
    limit: max results to return
    """
    company = _company_name(ticker)
    start   = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end     = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "filters": {
            "keywords":        [company],
            "award_type_codes": CONTRACT_TYPE_CODES,
            "time_period":     [{"start_date": start, "end_date": end}],
        },
        "fields": [
            "Recipient Name",
            "Award Amount",
            "Awarding Agency",
            "Start Date",
            "Description",
        ],
        "sort":  "Award Amount",
        "order": "desc",
        "limit": limit,
        "page":  1,
    }

    try:
        resp = requests.post(
            f"{USASPENDING_BASE}/search/spending_by_award/",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json().get("results", [])
    except Exception as e:
        print(f"[contracts] USASpending fetch failed for {ticker} ({company}): {e}")
        return []

    results = []
    for award in raw:
        amount = award.get("Award Amount") or 0
        results.append({
            "ticker":      ticker,
            "company":     award.get("Recipient Name", company),
            "amount":      float(amount),
            "agency":      award.get("Awarding Agency", ""),
            "date":        (award.get("Start Date") or "")[:10],
            "description": (award.get("Description") or "").strip()[:120],
        })

    # Flag priority agencies
    for r in results:
        r["priority"] = any(
            agency.lower() in r["agency"].lower()
            for agency in PRIORITY_AGENCIES
        )

    print(f"[contracts] {len(results)} contract award(s) found for {ticker} ({company})")
    return results


def summarise_contracts(awards: list[dict]) -> str:
    if not awards:
        return "No recent federal contract awards found."

    total = sum(a["amount"] for a in awards)
    lines = [f"Recent federal contracts — {len(awards)} award(s), ${total:,.0f} total:"]

    for a in awards:
        priority_tag = " [PRIORITY AGENCY]" if a.get("priority") else ""
        amount_str   = f"${a['amount']:,.0f}" if a["amount"] else "amount undisclosed"
        desc         = a["description"] or "(no description)"
        lines.append(
            f"- {a['date'] or 'Unknown date'}: {amount_str} from {a['agency']}{priority_tag} — {desc}"
        )

    return "\n".join(lines)
