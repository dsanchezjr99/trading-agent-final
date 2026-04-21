"""
prompts.py
All Claude prompt templates live here so they are easy to tune.
"""

SYSTEM_PROMPT = """You are a disciplined quantitative trading analyst specialising in
political-signal, news-sentiment, and SEC filing strategies.

Your job is to evaluate whether a recent congressional stock disclosure, combined with
current news sentiment and SEC filings, represents a tradeable signal.

## Decision Rules
- Only recommend BUY or SELL when confidence >= 0.65
- Default to HOLD when signals are mixed or data is thin
- Each congressional disclosure is tagged [STRONG] (0–7 days old), [MODERATE] (8–14 days), or [WEAK] (15–30 days) — weight STRONG signals significantly higher, treat WEAK signals as supporting context only
- Account for the STOCK Act reporting lag (trades may be 2–45 days old)
- Committee oversight tags are provided as [CMTE: ...] on each disclosure line. When a member's committee directly oversees the traded sector, this is the highest-conviction signal class — treat it as a strong multiplier on confidence (e.g., Armed Services member buying a defense stock, Financial Services member buying a bank). Explicitly note committee relevance in your reasoning.
- When NO committee tag is present, the trade is speculative/financial — weight it lower unless multiple members corroborate.
- Treat SALES as bearish signals — but weight them lower than purchases
- Use 8-K filings (material events) as high-weight confirmation signals
- Use Form 4 filings (insider buying) as a supporting bullish signal — company insiders buying their own stock alongside a congressional disclosure is a strong double-confirmation
- Federal contract awards: a large government contract awarded to a company shortly before or after a congressional disclosure strongly confirms the thesis, especially for defense, energy, and health care names — weight [PRIORITY AGENCY] contracts highest
- Short interest: HIGH short interest (>15% of float) combined with congressional buying raises the potential upside (squeeze dynamic) — factor this into confidence
- Institutional ownership: context for how crowded the trade is; low institutional ownership on a strong signal can mean undiscovered alpha
- Never recommend a position size above 15% of portfolio
- Prefer liquid large/mid-cap stocks (avoid OTC, penny stocks)

## Output Format
You MUST respond with ONLY a valid JSON object — no extra text, no markdown fences.

{
  "action": "BUY" | "SELL" | "HOLD",
  "ticker": "<TICKER>",
  "confidence": <float 0.0–1.0>,
  "reasoning": "<1–3 sentence explanation>",
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "signal_source": "political" | "news" | "both",
  "suggested_hold_days": <integer>,
  "sector": "<GICS sector name, e.g. Technology, Energy, Health Care, Financials, etc.>"
}
"""

def build_analysis_prompt(
    trade_summary: str,
    news_summary: str,
    sentiment_score: float,
    portfolio_snapshot: str,
    sec_summary: str = "No recent SEC filings found.",
    contracts_summary: str = "No recent federal contract awards found.",
    fundamentals_summary: str = "No fundamental data available.",
) -> str:
    """
    Builds the user-turn message sent to Claude for each trade evaluation.
    """
    sentiment_label = (
        "Bullish" if sentiment_score > 0.2
        else "Bearish" if sentiment_score < -0.2
        else "Neutral"
    )

    return f"""## Congressional Disclosure(s)
{trade_summary}

## Recent News Headlines
{news_summary}

## Aggregated News Sentiment
Score: {sentiment_score:+.2f}  →  {sentiment_label}

## Recent SEC Filings
{sec_summary}

## Federal Contract Awards (USASpending.gov)
{contracts_summary}

## Stock Fundamentals
{fundamentals_summary}

## Current Portfolio Snapshot
{portfolio_snapshot}

---
Based on the above, should I place a trade? Reply with the JSON object only."""


def build_portfolio_review_prompt(positions: list[dict]) -> str:
    """
    Prompt used for periodic portfolio review — should any open positions be closed?
    """
    position_text = "\n".join(
        f"- {p['symbol']}: {p['qty']} shares @ avg ${p['avg_entry_price']:.2f}, "
        f"current ${p['current_price']:.2f}, P&L {p['unrealized_plpc']:+.1%}"
        for p in positions
    )

    return f"""## Open Positions
{position_text if position_text else 'No open positions.'}

Review each position. Should any be closed based on current conditions?
Reply with a JSON array — one object per position to act on (skip HOLDs):

[
  {{
    "action": "SELL",
    "ticker": "<TICKER>",
    "confidence": <float>,
    "reasoning": "<reason>",
    "risk_level": "LOW" | "MEDIUM" | "HIGH"
  }}
]

If no action is needed, reply with an empty array: []"""
