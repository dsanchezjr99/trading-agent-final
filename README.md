# Congressional Trading Agent

An autonomous AI trading agent that monitors congressional stock disclosures and makes data-driven investment decisions by combining political signals, real-time news sentiment, and SEC filing analysis.

---

## Overview

Members of Congress are required by the STOCK Act to publicly disclose stock trades within 45 days of execution. This agent monitors those disclosures continuously and evaluates each one as a potential trading signal — cross-referencing it against live news sentiment, SEC filings, and broader market conditions before deciding whether to act.

Every decision is made by Claude (Anthropic's AI model), which weighs the strength and context of the signal and returns a structured BUY, SELL, or HOLD recommendation with a confidence score. That recommendation then passes through a multi-layer risk management system before any order is placed.

---

## Signal Pipeline

Each potential trade goes through the following evaluation process:

**1. Signal Sourcing**
Congressional disclosures are pulled from multiple data sources and filtered down to high-conviction signals only — trades above a minimum dollar threshold and filed within a relevant time window. Small routine trades and stale disclosures are automatically discarded.

**2. Market Context**
Before evaluating any ticker, the agent checks the broader market regime. In a confirmed downtrend, the confidence requirement for new positions is raised. Any ticker with earnings within a 5-day window is skipped entirely to avoid binary event risk.

**3. Multi-Source Research**
For each qualifying ticker, the agent simultaneously pulls:
- Real-time news headlines and sentiment scores
- Recent SEC filings (8-K material events, Form 4 insider trades, 10-Q reports)
- Historical price data to calculate realized volatility

**4. AI Decision**
Claude receives the complete research package and returns a structured decision including action, confidence score, risk level, sector classification, and suggested hold duration.

**5. Risk Management**
Every AI decision passes through a series of validation gates:
- Minimum confidence threshold (raised automatically in bear markets)
- Sector concentration limit — no more than 2 positions in the same sector
- Volatility-adjusted position sizing — high-volatility stocks receive proportionally smaller allocations
- Maximum open position count
- Daily loss circuit breaker

**6. Order Execution**
Approved trades are placed as limit orders to minimize slippage, with a market order fallback.

**7. Ongoing Monitoring**
Open positions are reviewed every hour. The agent automatically closes positions that hit a stop-loss, reach the take-profit target, or exceed their intended hold window. Claude reviews remaining positions and recommends closes based on current conditions.

---

## Risk Controls

| Control | Purpose |
|---------|---------|
| Stop-loss | Closes any position down more than 5% |
| Take-profit | Locks in gains when a position is up 20% |
| Daily loss limit | Halts all new trades if the portfolio is down 3% on the day |
| Sector cap | Max 2 positions in any single GICS sector |
| Position sizing | Scales down for high-volatility assets |
| Confidence gate | AI must meet a minimum confidence threshold to trigger a trade |
| Earnings buffer | Skips tickers with earnings within 5 days |
| Market regime | Raises confidence requirement in bear market conditions |

---

## Logging

Every decision, order, and close event is written to a structured JSON log file. Each entry includes the full AI reasoning, signal source, confidence score, realized P&L on close, and all relevant market data at the time of the decision. Logs rotate daily.

---

## Risk Disclaimer

This agent places real trades with real money when configured to do so. Congressional disclosure data has a reporting lag and is not a guaranteed forward-looking signal. Past performance of any trading strategy does not guarantee future results. Use at your own risk.
