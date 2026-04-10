# Congressional Trading Agent

An AI-powered trading agent that monitors congressional stock disclosures and uses Claude to decide whether to act on political signals combined with news sentiment and SEC filings.

---

## How It Works

1. Every 30 minutes, fetches recent congressional stock trades (House + Senate filings, optionally Quiver Quant)
2. For each new ticker, pulls news headlines (Polygon.io), sentiment scores (Alpha Vantage), and recent SEC filings (sec-api.io)
3. Sends everything to Claude, which returns a structured BUY / SELL / HOLD decision
4. Passes the decision through a risk manager (confidence gate, position limits, daily loss limit)
5. Places the order via Alpaca if approved
6. Every 60 minutes, reviews open positions and closes anything that hit stop-loss or that Claude recommends exiting

---

## Step 1 — Install Python Dependencies

Requires Python 3.11+.

```bash
pip install -r requirements.txt
```

---

## Step 2 — Get Your API Keys

Open `.env` and fill in each value. Details for each key below.

### Required

#### Anthropic (Claude AI)
The agent's decision engine. Without this, nothing works.

1. Go to [console.anthropic.com/account/keys](https://console.anthropic.com/account/keys)
2. Create a new API key
3. Add credits to your account — Claude Opus usage will cost roughly $0.01–$0.05 per trade evaluation
4. Paste the key into `ANTHROPIC_API_KEY=`

#### Alpaca (Broker)
Executes orders. Start with a paper trading account — no real money required.

1. Sign up at [alpaca.markets](https://alpaca.markets)
2. Go to **Paper Trading** → **API Keys** → generate a key pair
3. Paste into `ALPACA_API_KEY=` and `ALPACA_SECRET_KEY=`
4. Leave `ALPACA_BASE_URL=https://paper-api.alpaca.markets` for now

### Optional (but strongly recommended)

More data sources = better signals. The agent works without these but Claude will have less context.

#### Polygon.io (news headlines)
Financial news articles tied directly to tickers. Free tier includes unlimited delayed data and news — sufficient for this agent.

1. Go to [polygon.io](https://polygon.io) and sign up
2. Free tier: unlimited API calls with 15-minute delayed data and full news access
3. Paid plans start at $29/month for real-time data — not required for this strategy
4. Paste into `POLYGON_API_KEY=`

#### Alpha Vantage (news sentiment scores)
Provides numeric sentiment scores attached to financial news articles. This is the primary sentiment signal used to quantify market mood.

1. Go to [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key)
2. The free tier gives 25 requests/day and 5/min — enough for light use
3. For heavier use, paid plans start at $50/month
4. Paste into `ALPHA_VANTAGE_API_KEY=`

#### Quiver Quantitative (congressional trade data)
A cleaner, more reliable congressional trade feed than the raw House/Senate S3 endpoints. Has better historical data and fewer parsing issues.

1. Go to [quiverquant.com/subscribe](https://www.quiverquant.com/subscribe)
2. Plans start at $25/month — worth it if you're running this seriously
3. Paste into `QUIVER_API_KEY=`

> **Without Quiver:** The agent falls back to the free House Stock Watcher and Senate Stock Watcher S3 feeds. These work but can have delays and occasionally malformed data.

#### sec-api.io (SEC EDGAR filings)
Pulls recent 8-K (material events), 10-Q (quarterly reports), and Form 4 (insider trades) filings for each ticker. 8-K filings in particular are strong confirmation signals for congressional trades — a purchase followed by a positive 8-K is a much higher-conviction setup than either signal alone.

1. Go to [sec-api.io](https://sec-api.io) and register
2. Free tier: 100 requests/month — enough to evaluate a few dozen tickers
3. Paid plans start at $49/month for higher volume
4. Paste into `SEC_API_KEY=`

---

## Step 3 — Configure Agent Settings

These are in `.env` and have sensible defaults. Review them before running.

| Setting | Default | What It Does |
|---|---|---|
| `PORTFOLIO_VALUE` | `500` | Dollar value used to calculate position sizes. Set this to your actual Alpaca account balance. |
| `MAX_POSITION_PCT` | `0.15` | Max 15% of portfolio per trade. Hard-capped at $75 regardless. |
| `MAX_OPEN_POSITIONS` | `5` | Agent won't open a 6th position until one closes. |
| `STOP_LOSS_PCT` | `0.05` | Auto-close any position down more than 5%. |
| `MAX_DAILY_LOSS_PCT` | `0.03` | Halt all new trades for the day if the portfolio is down 3% from open. |
| `MIN_CONFIDENCE` | `0.70` | Claude must be at least 70% confident to trigger a trade. |
| `POLL_INTERVAL_MINUTES` | `30` | How often to check for new congressional disclosures. |
| `DRY_RUN` | `true` | Paper mode — logs what would happen but never places real orders. |

---

## Step 4 — Test in Paper Mode

With `DRY_RUN=true` (the default), the agent will run through the full decision loop but print `DRY RUN` instead of placing real orders.

```bash
python agent/main.py
```

You should see output like:

```
[agent] Starting trading agent (poll every 30 min, DRY_RUN=true)
[political] Found 12 unique trades (last 7 days)
[agent] 3 new ticker(s) to evaluate: NVDA, MSFT, LMT
[agent] Evaluating NVDA...
[news] 6 articles found for NVDA
[risk] Approved: BUY $56.25 of NVDA (confidence 85%, risk MEDIUM)
[broker] DRY RUN — would BUY $56.25 of NVDA
```

**Check the logs.** Each run creates a daily log file at `logs/trades_YYYY-MM-DD.jsonl` with every analysis and decision in structured JSON.

**Run it for a few days in dry-run mode** to get a feel for how many signals it generates, what Claude's decisions look like, and whether the risk settings make sense for you.

---

## Step 5 — Go Live

Once you're satisfied with the dry-run behavior:

### 1. Fund your Alpaca account
Deposit real money into your Alpaca live account at [alpaca.markets](https://alpaca.markets). Minimum is $0 but fractional shares require at least $1 per order.

Update `PORTFOLIO_VALUE` in `.env` to match your actual account balance.

### 2. Switch to live API keys
In `.env`:
- Get **live** API keys from Alpaca (separate from paper keys) under **Live Trading** → **API Keys**
- Update `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` with the live keys
- Change `ALPACA_BASE_URL=https://api.alpaca.markets`

### 3. Flip the switch
In `.env`, change:
```
DRY_RUN=false
```

### 4. Run it
```bash
python agent/main.py
```

Real orders will now be submitted. Watch the first few trades carefully to make sure everything behaves as expected.

---

## Keeping It Running

The agent runs as a foreground process. To keep it alive on a Mac:

**Using a terminal multiplexer (simplest):**
```bash
brew install tmux
tmux new -s agent
python agent/main.py
# Detach with Ctrl+B, then D
# Reattach later with: tmux attach -t agent
```

**Using launchd (runs on login, Mac-native):**

Create `~/Library/LaunchAgents/com.trading-agent.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.trading-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/dsj/trading-agent/agent/main.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/dsj/trading-agent</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/dsj/trading-agent/logs/agent.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/dsj/trading-agent/logs/agent.log</string>
</dict>
</plist>
```

Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.trading-agent.plist
```

---

## File Structure

```
trading-agent/
├── agent/
│   ├── main.py          # Main loop, scheduler, Claude calls
│   ├── broker.py        # Alpaca order placement and portfolio queries
│   ├── risk_manager.py  # Trade validation gates and position sizing
│   └── prompts.py       # Claude prompt templates
├── data/
│   ├── political.py     # Congressional trade data (House/Senate/Quiver Quant)
│   ├── news.py          # News headlines (Polygon.io) + sentiment scores (Alpha Vantage)
│   └── sec.py           # SEC EDGAR filings (sec-api.io)
├── logs/                # Created automatically on first run
│   ├── trades_YYYY-MM-DD.jsonl   # Daily trade logs
│   └── seen_disclosures.json     # Persistent state (survives restarts)
├── utils.py             # Shared retry and rate-limiting utilities
├── requirements.txt
└── .env                 # Your keys and settings (never commit this)
```

---

## Risk Disclaimer

This agent places real trades with real money if `DRY_RUN=false`. Congressional disclosure data has a reporting lag of up to 45 days and is not a reliable short-term signal. Past performance of political trading strategies does not guarantee future results. Use at your own risk.
