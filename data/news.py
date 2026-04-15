"""
news.py
Fetches news headlines and sentiment scores for a given ticker from:
  - Alpaca News Feed (free with any Alpaca account, no rate limit)
  - Polygon.io (financial news headlines)
  - Alpha Vantage (financial news + sentiment scores)
"""

import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

from utils import fetch_with_retry, RateLimiter

load_dotenv()

ALPACA_KEY        = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET     = os.getenv("ALPACA_SECRET_KEY")
POLYGON_KEY       = os.getenv("POLYGON_API_KEY")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

ALPACA_NEWS_BASE = "https://data.alpaca.markets/v1beta1/news"
POLYGON_BASE     = "https://api.polygon.io"
AV_BASE          = "https://www.alphavantage.co/query"

# Alpha Vantage free tier: 25 requests/day, ~5/min — enforce 12-second spacing
_av_limiter = RateLimiter(calls_per_minute=5)


def _fetch_json(url: str, **kwargs) -> dict:
    resp = requests.get(url, **kwargs)
    resp.raise_for_status()
    return resp.json()


# ── Alpaca News Feed ──────────────────────────────────────────────────────────

def get_alpaca_news(ticker: str, limit: int = 10) -> list[dict]:
    """
    Fetch recent news articles for a ticker from Alpaca's News Feed.
    Uses the same ALPACA_API_KEY + ALPACA_SECRET_KEY already in .env — no extra account needed.
    No rate limit. Returns real-time headlines tied directly to the ticker.
    """
    if not ALPACA_KEY or ALPACA_KEY == "your_alpaca_api_key_here":
        print("[news] Alpaca API key not set — skipping Alpaca news.")
        return []

    headers = {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

    try:
        data = fetch_with_retry(lambda: _fetch_json(
            ALPACA_NEWS_BASE,
            headers=headers,
            params={
                "symbols": ticker,
                "limit":   limit,
                "sort":    "desc",
                "include_content": "false",
            },
            timeout=15,
        ))
    except Exception as e:
        print(f"[news] Alpaca news error for {ticker}: {e}")
        return []

    return [
        {
            "source":       "alpaca",
            "ticker":       ticker,
            "title":        item.get("headline", ""),
            "description":  item.get("summary", ""),
            "url":          item.get("url", ""),
            "published_at": item.get("created_at", ""),
            "sentiment":    None,
        }
        for item in data.get("news", [])
        if item.get("headline")
    ]


# ── Polygon.io ────────────────────────────────────────────────────────────────

def get_polygon_news(ticker: str, limit: int = 10) -> list[dict]:
    """
    Fetch recent news articles for a ticker from Polygon.io.
    Requires POLYGON_API_KEY in .env
    Free tier: unlimited delayed data and news.
    """
    if not POLYGON_KEY or POLYGON_KEY == "your_polygon_api_key_here":
        print("[news] Polygon.io key not set — skipping.")
        return []

    try:
        data = fetch_with_retry(lambda: _fetch_json(
            f"{POLYGON_BASE}/v2/reference/news",
            params={
                "ticker": ticker,
                "limit":  limit,
                "sort":   "published_utc",
                "order":  "desc",
                "apiKey": POLYGON_KEY,
            },
            timeout=15,
        ))
    except Exception as e:
        print(f"[news] Polygon news error for {ticker}: {e}")
        return []

    return [
        {
            "source":       "polygon",
            "ticker":       ticker,
            "title":        item.get("title", ""),
            "description":  item.get("description", ""),
            "url":          item.get("article_url", ""),
            "published_at": item.get("published_utc", ""),
            "sentiment":    None,  # Polygon news doesn't include sentiment scores
        }
        for item in data.get("results", [])
        if item.get("title")
    ]


# ── Alpha Vantage ─────────────────────────────────────────────────────────────

def get_alphavantage_sentiment(ticker: str) -> list[dict]:
    """
    Fetch financial news + AI sentiment scores from Alpha Vantage.
    Requires ALPHA_VANTAGE_API_KEY in .env
    Sentiment labels: Bullish / Somewhat-Bullish / Neutral / Somewhat-Bearish / Bearish
    """
    if not ALPHA_VANTAGE_KEY or ALPHA_VANTAGE_KEY == "your_alpha_vantage_key_here":
        print("[news] Alpha Vantage key not set — skipping.")
        return []

    try:
        _av_limiter.wait()
        data = fetch_with_retry(lambda: _fetch_json(
            AV_BASE,
            params={
                "function": "NEWS_SENTIMENT",
                "tickers":  ticker,
                "limit":    10,
                "apikey":   ALPHA_VANTAGE_KEY,
            },
            timeout=15,
        ))
    except Exception as e:
        print(f"[news] Alpha Vantage error for {ticker}: {e}")
        return []

    if "feed" not in data:
        print(f"[news] Alpha Vantage returned no feed for {ticker}: {data.get('Note', data.get('Information', ''))}")
        return []

    results = []
    for item in data["feed"]:
        ticker_sentiment = next(
            (ts for ts in item.get("ticker_sentiment", []) if ts.get("ticker") == ticker),
            {}
        )
        results.append({
            "source":          "alphavantage",
            "ticker":          ticker,
            "title":           item.get("title", ""),
            "description":     item.get("summary", ""),
            "url":             item.get("url", ""),
            "published_at":    item.get("time_published", ""),
            "sentiment":       ticker_sentiment.get("ticker_sentiment_label", "Neutral"),
            "sentiment_score": float(ticker_sentiment.get("ticker_sentiment_score", 0.0)),
            "relevance_score": float(ticker_sentiment.get("relevance_score", 0.0)),
        })

    return results


# ── Combined entry point ──────────────────────────────────────────────────────

def get_news_for_ticker(ticker: str) -> list[dict]:
    """Merge and sort news from all sources for a given ticker."""
    articles = (
        get_alpaca_news(ticker)
        + get_polygon_news(ticker)
        + get_alphavantage_sentiment(ticker)
    )
    articles.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    print(f"[news] {len(articles)} articles found for {ticker}")
    return articles


# ── Summary helpers used by Claude prompt ────────────────────────────────────

def summarise_news(articles: list[dict]) -> str:
    """Format news list into a readable string for the AI prompt."""
    if not articles:
        return "No recent news found."

    lines = []
    for a in articles[:8]:  # Limit to 8 headlines to keep prompt concise
        sentiment_tag = f" [{a['sentiment']}]" if a.get("sentiment") else ""
        lines.append(f"- {a['title']}{sentiment_tag} ({a['published_at'][:10]})")
    return "\n".join(lines)


def aggregate_sentiment_score(articles: list[dict]) -> float:
    """
    Returns a float from -1.0 (very bearish) to +1.0 (very bullish).
    Only uses Alpha Vantage articles that have a numeric sentiment_score.
    """
    scored = [a for a in articles if a.get("sentiment_score") is not None]
    if not scored:
        return 0.0
    return sum(a["sentiment_score"] for a in scored) / len(scored)
