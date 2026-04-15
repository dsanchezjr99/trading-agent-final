# Congressional Trading Agent

An autonomous AI trading agent that monitors congressional stock disclosures and makes data-driven investment decisions by combining political signals, real-time news sentiment, and SEC filing analysis.

---

## Overview

Members of Congress are required by the STOCK Act to publicly disclose stock trades within 45 days of execution. This agent monitors those disclosures continuously and evaluates each one as a potential trading signal — cross-referencing it against live news sentiment, SEC filings, and broader market conditions before deciding whether to act.

Every decision is made by Claude (Anthropic's AI model), which weighs the strength and context of the signal and returns a structured BUY, SELL, or HOLD recommendation with a confidence score. That recommendation then passes through a multi-layer validation and risk management system before any order is placed.

---

## Signal Pipeline

Each potential trade goes through the following evaluation process:

**1. Signal Sourcing**
Congressional disclosures are pulled from multiple data sources and filtered down to high-conviction signals only — trades above a minimum dollar threshold and filed within a relevant time window. Small routine trades and stale disclosures are automatically discarded. Each disclosure is tagged with a signal strength label (Strong, Moderate, or Weak) based on how recently the transaction occurred, so newer signals carry more weight.

**2. Signal Gate (Validation Layer 1)**
Before any ticker is researched, the raw disclosures for that ticker are evaluated against a rules-based gate:
- At least 2 unique congressional members must be buyers
- Buyers must outnumber sellers (net bullish directional bias)
- The aggregate disclosed minimum purchase amount must reach a set threshold

Tickers that don't meet all three criteria are skipped without calling the AI.

**3. Market Context**
Before evaluating any ticker, the agent checks the broader market regime. In a confirmed downtrend, the confidence requirement for new positions is raised. Any ticker with earnings within a 5-day window is skipped entirely to avoid binary event risk.

**4. Liquidity Filter (Validation Layer 2)**
The agent checks the ticker's average daily dollar volume. Tickers that trade below a minimum volume threshold are excluded to ensure that the position size being placed is not a significant fraction of normal daily activity.

**5. Momentum Gate (Validation Layer 3)**
The ticker's current price is checked against its 20-day moving average. If the stock is trading below its own recent trend, the signal is skipped regardless of how many members bought — this prevents buying into a downtrend.

**6. Multi-Source Research**
For each ticker that clears all gates, the agent simultaneously pulls:
- Real-time news headlines and sentiment scores (Alpaca News, Polygon, Alpha Vantage)
- Recent SEC filings (8-K material events, Form 4 insider trades, 10-Q reports)
- Historical price data to calculate realized volatility

**7. AI Decision**
Claude receives the complete research package — including signal strength labels, gate outcomes, news sentiment, and SEC data — and returns a structured decision including action, confidence score, risk level, sector classification, and suggested hold duration.

**8. Risk Management**
Every AI decision passes through a series of validation gates before an order is placed:
- Minimum confidence threshold (raised automatically in bear markets)
- Sector concentration limit — no more than 2 positions in the same sector
- Volatility-adjusted position sizing — high-volatility stocks receive proportionally smaller allocations
- Hard cap on dollar amount per position
- Maximum open position count
- Daily loss circuit breaker

**9. Order Execution**
Approved trades are placed as limit orders to minimize slippage, with a market order fallback.

**10. Ongoing Monitoring**
Open positions are reviewed every 30 minutes. The agent automatically closes positions that hit a stop-loss, reach the take-profit target, or exceed their intended hold window. Claude reviews remaining positions and recommends closes based on current conditions.

---

## Risk Controls

| Control | Detail |
|---------|--------|
| Stop-loss | Closes any position down more than 5% |
| Take-profit | Locks in gains when a position is up 20% |
| Daily loss limit | Halts all new trades if the portfolio is down 3% on the day |
| Sector cap | Max 2 positions in any single GICS sector |
| Position sizing | Scales down for high-volatility assets; hard cap per trade |
| Confidence gate | AI must meet a minimum confidence threshold to trigger a trade |
| Earnings buffer | Skips tickers with earnings within 5 days |
| Market regime | Raises confidence requirement in bear market conditions |
| Signal gate | Requires multi-member buying consensus and minimum aggregate amount |
| Momentum gate | Skips tickers trading below their 20-day moving average |
| Liquidity filter | Skips tickers below a minimum average daily dollar volume |

---

## Notifications

The agent sends email alerts throughout the day:
- **Trade placed** — ticker, direction, size, and confidence score
- **Position closed** — exit reason (stop-loss, take-profit, hold expiry, AI close) and realized P&L
- **Market open** — morning summary of open positions
- **End of day** — daily P&L summary and number of trades executed
- **Circuit breaker triggered** — alert when the daily loss limit halts new trades
- **Bear market detected** — alert when the market regime shifts to defensive mode

---

## Logging

Every decision, order, and close event is written to a structured JSON log file. Each entry includes the full AI reasoning, signal source, confidence score, realized P&L on close, and all relevant market data at the time of the decision. Logs rotate daily.

---

## Risk Disclaimer

This agent places real trades with real money when configured to do so. Congressional disclosure data has a reporting lag and is not a guaranteed forward-looking signal. Past performance of any trading strategy does not guarantee future results. Use at your own risk.
