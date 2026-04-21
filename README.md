# Congressional Trading Agent

An autonomous AI trading agent that monitors congressional stock disclosures and makes data-driven investment decisions by combining political signals, committee oversight context, real-time news sentiment, and SEC filing analysis.

---

## Overview

Members of Congress are required by the STOCK Act to publicly disclose stock trades within 45 days of execution. This agent monitors those disclosures continuously and evaluates each one as a potential trading signal — cross-referencing it against live news sentiment, SEC filings, and broader market conditions before deciding whether to act.

The core edge: when a member of the Armed Services Committee buys a defense stock, or a member of the Financial Services Committee buys a bank, that is a qualitatively different signal than a random member making the same trade. The agent detects and weights this committee-sector overlap explicitly.

Every decision is made by Claude (Anthropic's AI model), which weighs signal strength, committee context, news sentiment, and SEC data, then returns a structured BUY, SELL, or HOLD recommendation with a confidence score. That recommendation passes through a multi-layer validation and risk management system before any order is placed.

---

## Signal Pipeline

Each potential trade goes through the following evaluation process:

**1. Signal Sourcing**
Congressional disclosures are pulled every 5 minutes from three sources (House Stock Watcher, Senate Stock Watcher, Quiver Quantitative) and filtered to high-conviction signals only — trades above a minimum dollar threshold filed within a relevant time window. Stale disclosures and small routine trades are discarded automatically.

Each disclosure is tagged with two pieces of metadata:
- **Signal strength** — `[STRONG]` (0–7 days old), `[MODERATE]` (8–14 days), `[WEAK]` (15–30 days). Newer signals carry significantly more weight.
- **Committee tag** — `[CMTE: Armed Services, Intelligence]` — pulled from the ProPublica Congress API (or the static 119th Congress fallback). Flags when the trading member sits on a committee with direct oversight of the sector they're trading in.

**2. Signal Gate (Validation Layer 1)**
Before any ticker is researched, the raw disclosures are evaluated against a rules-based gate:
- At least 2 unique congressional members must be buyers
- Buyers must outnumber sellers (net bullish directional bias)
- The aggregate disclosed minimum purchase amount must reach a set threshold

Tickers that don't meet all three criteria are skipped without calling the AI.

**3. Market Context**
The agent checks the broader market regime against SPY's 50-day moving average. In a confirmed downtrend, the confidence requirement for new positions is raised automatically. Any ticker with earnings within a 5-day window is skipped entirely to avoid binary event risk.

**4. Liquidity Filter (Validation Layer 2)**
The ticker's 20-day average daily dollar volume is checked. Tickers below the minimum threshold are excluded to ensure the position size is not a meaningful fraction of normal daily activity.

**5. Momentum Gate (Validation Layer 3)**
The ticker's current price is checked against its 20-day moving average. If the stock is trading below its own recent trend, the signal is skipped regardless of how many members bought — this prevents buying into a confirmed downtrend.

**6. Multi-Source Research**
For each ticker that clears all gates, the agent simultaneously fetches:
- Real-time news headlines and sentiment scores (Alpaca News, Alpha Vantage)
- Recent SEC filings (8-K material events, Form 4 insider trades, 10-Q reports)
- Historical price data to calculate realized annualized volatility

**7. AI Decision**
Claude receives the complete research package — signal strength labels, committee tags, news sentiment, SEC data, and portfolio state — and returns a structured decision:
- `action`: BUY / SELL / HOLD
- `confidence`: 0.0–1.0
- `risk_level`: LOW / MEDIUM / HIGH
- `sector`: GICS sector classification
- `suggested_hold_days`: intended holding period
- `reasoning`: 1–3 sentence explanation

Claude is explicitly instructed to treat committee-relevant trades as the highest signal class. A member of the Armed Services Committee buying a defense stock carries materially more weight than an unaffiliated member making the same trade.

**8. Risk Management**
Every AI decision passes through 7 sequential validation gates before an order is placed:
- HOLD passthrough — AI-recommended holds never reach the broker
- Minimum confidence threshold (raised automatically in bear markets)
- HIGH risk level rejection
- Maximum open position count
- No duplicate positions
- Sector concentration limit — max 2 positions in the same GICS sector
- Invalid ticker rejection

Position size is then calculated as: `portfolio_value × MAX_POSITION_PCT`, scaled by confidence and volatility, capped at `portfolio_value × HARD_CAP_PCT`. The hard cap grows automatically as the account grows — there is no fixed dollar ceiling.

**9. Order Execution**
Approved trades are placed as bracket limit orders — the stop-loss and take-profit legs are submitted to Alpaca server-side at the time of entry, so exits are handled in real-time without polling.

**10. Ongoing Monitoring**
Open positions are reviewed every 15 minutes. The agent closes positions that hit a stop-loss, reach take-profit, or exceed their intended hold window. Claude reviews remaining positions and recommends closes based on current conditions.

---

## Committee Oversight Strategy

The agent maintains a committee membership map for the 119th Congress (2025–2027) covering the chairs, ranking members, and active traders on the following oversight committees:

| Committee | Sector Overlap |
|-----------|---------------|
| Armed Services (House + Senate) | Defense, Aerospace & Defense |
| Intelligence (House + Senate) | Defense, Technology |
| Financial Services / Banking | Financials, Real Estate |
| Energy & Commerce | Energy, Technology, Health Care |
| Energy & Natural Resources | Energy, Utilities |
| Health, Education, Labor & Pensions | Health Care, Pharma & Biotech |
| Ways & Means | Financials, Health Care |
| Appropriations | Defense, Health Care, Industrials |
| Commerce, Science & Transportation | Technology, Industrials |
| Science, Space & Technology | Technology, Aerospace |

When a member's committee maps to the sector of the stock they traded, Claude treats this as the strongest possible signal class — these members have non-public knowledge of contracts, regulations, and budget decisions in their oversight domain.

The committee data is sourced from the **unitedstates/congress-legislators** project on GitHub — no API key required. The agent fetches the live `committee-membership-current.yaml` on startup, caches it to disk, and falls back to a static 119th Congress map if the fetch fails.

---

## Risk Controls

| Control | Detail |
|---------|--------|
| Stop-loss | Closes any position down more than 5% |
| Take-profit | Locks in gains when a position is up 20% |
| Daily loss limit | Halts all new trades if the portfolio is down 3% on the day |
| Sector cap | Max 2 positions in any single GICS sector |
| Position sizing | Volatility-adjusted; hard cap scales with portfolio value |
| Confidence gate | Minimum threshold; raised automatically in bear market |
| Earnings buffer | Skips tickers with earnings within 5 days |
| Market regime | SPY vs 50-day MA — raises confidence requirement in downtrend |
| Signal gate | Requires multi-member buying consensus and minimum aggregate amount |
| Momentum gate | Skips tickers trading below their 20-day moving average |
| Liquidity filter | Skips tickers below a minimum average daily dollar volume |
| Bracket orders | Stop-loss and take-profit legs placed server-side at entry |

---

## Data Sources

| Source | Used For |
|--------|----------|
| House Stock Watcher | Congressional trade disclosures (House) |
| Senate Stock Watcher | Congressional trade disclosures (Senate) |
| Quiver Quantitative | Supplementary congressional trade data |
| unitedstates/congress-legislators | Committee membership (GitHub, no key required) |
| Alpaca Data API | Price bars, market data, order execution |
| Alpha Vantage | News headlines and sentiment scores |
| SEC EDGAR | 8-K, Form 4, and 10-Q filings |
| yfinance | Earnings calendar |

---

## Notifications

The agent sends email alerts throughout the day:
- **Trade placed** — ticker, direction, size, confidence score, and reasoning
- **Position closed** — exit reason (stop-loss, take-profit, hold expiry, AI close) and P&L
- **Market open** — morning summary of open positions
- **End of day** — daily P&L summary and trade count
- **Circuit breaker triggered** — alert when the daily loss limit halts new trades
- **Bear market detected** — alert when the market regime shifts to defensive mode

---

## Logging

Every decision, order, and close event is written to a structured JSONL log file. Each entry includes the full AI reasoning, signal source, committee context, confidence score, realized P&L on close, and all relevant market data at the time of the decision. Logs rotate daily.

---

## Configuration

Key settings in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL_MINUTES` | `5` | How often to check for new disclosures |
| `MAX_POSITION_PCT` | `0.15` | Max position size as % of portfolio |
| `HARD_CAP_PCT` | `0.15` | Hard ceiling per trade as % of portfolio (scales with account) |
| `MIN_CONFIDENCE` | `0.65` | Minimum AI confidence to place a trade |
| `STOP_LOSS_PCT` | `0.05` | Stop-loss threshold |
| `TAKE_PROFIT_PCT` | `0.20` | Take-profit threshold |
| `MAX_DAILY_LOSS_PCT` | `0.03` | Daily drawdown limit before halting |
| `MAX_OPEN_POSITIONS` | `8` | Max concurrent positions |
| `MAX_SECTOR_POSITIONS` | `2` | Max positions per GICS sector |
| `MIN_TRADE_AMOUNT` | `15000` | Minimum congressional trade size to consider |
| — | — | Committee data fetched automatically from GitHub, no key needed |

---

## Risk Disclaimer

This agent places real trades with real money when configured to do so. Congressional disclosure data has a reporting lag of up to 45 days and is not a guaranteed forward-looking signal. Committee membership data improves signal quality but does not eliminate risk. Past performance of any trading strategy does not guarantee future results. Use at your own risk.
