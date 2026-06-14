"""Fetch global market context for regime analysis.

Sources:
- US markets: yfinance (S&P 500, Nasdaq, Dow)
- FII/DII: NSE live API (today only) + NSE archive CSV (historical)
- GIFT Nifty gap: yfinance SGX Nifty futures
- India news: Economic Times RSS
- Global news: Reuters RSS (geopolitical / macro keywords)
- US earnings: yfinance earnings calendar
- Economic calendar: hardcoded high-impact event dates

All fetches are best-effort — failures return None, never raise.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, timedelta, datetime
from typing import Any

import httpx

# ── Economic event calendar ────────────────────────────────────────────────
_HIGH_IMPACT_EVENTS: list[tuple[date, str]] = [
    # RBI MPC 2026
    (date(2026, 2, 7),   "RBI MPC Rate Decision"),
    (date(2026, 4, 9),   "RBI MPC Rate Decision"),
    (date(2026, 6, 6),   "RBI MPC Rate Decision"),
    (date(2026, 8, 8),   "RBI MPC Rate Decision"),
    (date(2026, 10, 8),  "RBI MPC Rate Decision"),
    (date(2026, 12, 5),  "RBI MPC Rate Decision"),
    # US FOMC 2026
    (date(2026, 1, 29),  "US FOMC Rate Decision"),
    (date(2026, 3, 19),  "US FOMC Rate Decision"),
    (date(2026, 5, 7),   "US FOMC Rate Decision"),
    (date(2026, 6, 18),  "US FOMC Rate Decision"),
    (date(2026, 7, 30),  "US FOMC Rate Decision"),
    (date(2026, 9, 17),  "US FOMC Rate Decision"),
    (date(2026, 11, 5),  "US FOMC Rate Decision"),
    (date(2026, 12, 16), "US FOMC Rate Decision"),
    # India budget / policy
    (date(2026, 2, 1),   "India Union Budget"),
    # US CPI 2026 (approx mid-month)
    (date(2026, 1, 15),  "US CPI Data"),
    (date(2026, 2, 12),  "US CPI Data"),
    (date(2026, 3, 12),  "US CPI Data"),
    (date(2026, 4, 10),  "US CPI Data"),
    (date(2026, 5, 13),  "US CPI Data"),
    (date(2026, 6, 11),  "US CPI Data"),
    (date(2026, 7, 15),  "US CPI Data"),
    (date(2026, 8, 13),  "US CPI Data"),
    (date(2026, 9, 10),  "US CPI Data"),
    (date(2026, 10, 14), "US CPI Data"),
    (date(2026, 11, 12), "US CPI Data"),
    (date(2026, 12, 10), "US CPI Data"),
    # US Non-Farm Payrolls 2026 (first Friday of month)
    (date(2026, 1, 9),   "US Non-Farm Payrolls"),
    (date(2026, 2, 6),   "US Non-Farm Payrolls"),
    (date(2026, 3, 6),   "US Non-Farm Payrolls"),
    (date(2026, 4, 3),   "US Non-Farm Payrolls"),
    (date(2026, 5, 1),   "US Non-Farm Payrolls"),
    (date(2026, 6, 5),   "US Non-Farm Payrolls"),
    (date(2026, 7, 10),  "US Non-Farm Payrolls"),
    (date(2026, 8, 7),   "US Non-Farm Payrolls"),
    (date(2026, 9, 4),   "US Non-Farm Payrolls"),
    (date(2026, 10, 2),  "US Non-Farm Payrolls"),
    (date(2026, 11, 6),  "US Non-Farm Payrolls"),
    (date(2026, 12, 4),  "US Non-Farm Payrolls"),
]

# News keywords that indicate high market impact for NSE
_INDIA_HIGH_IMPACT_KEYWORDS = [
    "rbi", "repo rate", "inflation", "gdp", "budget", "fiscal",
    "fii", "fpi", "foreign", "rupee", "crude", "oil", "sebi",
    "nse", "sensex", "nifty", "market crash", "circuit breaker",
]
_GLOBAL_HIGH_IMPACT_KEYWORDS = [
    "fed", "federal reserve", "rate hike", "rate cut", "tariff",
    "trade war", "sanctions", "war", "ceasefire", "recession",
    "inflation", "oil price", "opec", "china", "ukraine",
    "israel", "iran", "geopolit",
]

# Major US tickers whose earnings move global markets
_MARKET_MOVING_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "JPM", "GS"]


def check_event_proximity(target_date: date, window_days: int = 1) -> list[str]:
    hits = []
    for ev_date, ev_name in _HIGH_IMPACT_EVENTS:
        if abs((target_date - ev_date).days) <= window_days:
            hits.append(ev_name)
    return hits


# ── US markets ─────────────────────────────────────────────────────────────

def fetch_us_market_close(target_date: date) -> dict[str, float | None]:
    """S&P 500, Nasdaq, Dow % change for the US session preceding target_date."""
    result: dict[str, float | None] = {"sp500_pct": None, "nasdaq_pct": None, "dow_pct": None}
    try:
        import yfinance as yf
        import pandas as pd
        start = (target_date - timedelta(days=10)).isoformat()
        end = target_date.isoformat()
        for key, ticker in [("sp500_pct", "^GSPC"), ("nasdaq_pct", "^IXIC"), ("dow_pct", "^DJI")]:
            try:
                df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
                if df is None or df.empty:
                    continue
                close_col = ("Close", ticker) if ("Close", ticker) in df.columns else "Close"
                closes = df[close_col].dropna()
                if isinstance(closes, pd.DataFrame):
                    closes = closes.iloc[:, 0]
                if len(closes) >= 2:
                    result[key] = round(float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100), 2)
            except Exception:
                pass
    except ImportError:
        pass
    return result


# ── GIFT Nifty / SGX gap ───────────────────────────────────────────────────

def fetch_gift_nifty_gap(target_date: date) -> dict[str, Any]:
    """Estimate pre-market GIFT Nifty gap using SGX Nifty futures (^SGXNIFTY) vs prior NIFTY close.

    Falls back to None if data unavailable.
    """
    result: dict[str, Any] = {"gift_nifty_pts": None, "gift_nifty_pct": None}
    try:
        import yfinance as yf
        import pandas as pd
        start = (target_date - timedelta(days=10)).isoformat()
        end = (target_date + timedelta(days=1)).isoformat()

        # SGX Nifty (GIFT Nifty proxy)
        sgx = yf.download("^SGXNIFTY", start=start, end=end, progress=False, auto_adjust=True)
        nifty = yf.download("^NSEI", start=start, end=end, progress=False, auto_adjust=True)

        def last_close(df, ticker):
            col = ("Close", ticker) if ("Close", ticker) in df.columns else "Close"
            s = df[col].dropna()
            if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
            return float(s.iloc[-1]) if len(s) >= 1 else None

        sgx_last = last_close(sgx, "^SGXNIFTY") if sgx is not None and not sgx.empty else None
        nifty_prev = None
        if nifty is not None and not nifty.empty:
            col = ("Close", "^NSEI") if ("Close", "^NSEI") in nifty.columns else "Close"
            s = nifty[col].dropna()
            if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
            # Get the close from the day before target_date
            s.index = pd.to_datetime(s.index)
            prev = s[s.index.date < target_date]
            if len(prev) >= 1:
                nifty_prev = float(prev.iloc[-1])

        if sgx_last and nifty_prev and nifty_prev > 0:
            pts = round(sgx_last - nifty_prev, 1)
            pct = round(pts / nifty_prev * 100, 2)
            result["gift_nifty_pts"] = pts
            result["gift_nifty_pct"] = pct
    except Exception:
        pass
    return result


# ── FII flow ───────────────────────────────────────────────────────────────

def fetch_fii_historical(target_date: date) -> dict[str, float | None]:
    """Fetch FII cash net flow from NSE archive CSV for a specific historical date."""
    result: dict[str, float | None] = {"fii_cash_net_cr": None, "fii_deriv_net_cr": None}
    try:
        # NSE archive format: DDMMYYYY
        date_str = target_date.strftime("%d%m%Y")
        url = f"https://archives.nseindia.com/content/fo/fii_stats_{date_str}.csv"
        resp = httpx.get(url, timeout=8, follow_redirects=True)
        if resp.status_code != 200:
            return result
        lines = resp.text.strip().splitlines()
        for line in lines[1:]:  # skip header
            cols = [c.strip().strip('"') for c in line.split(",")]
            if len(cols) < 4:
                continue
            try:
                net = float(cols[3].replace(",", ""))
                desc = cols[0].lower()
                if "deriv" in desc or "f&o" in desc or "future" in desc:
                    result["fii_deriv_net_cr"] = round(net, 0)
                elif "cash" in desc or "equity" in desc or result["fii_cash_net_cr"] is None:
                    result["fii_cash_net_cr"] = round(net, 0)
            except (ValueError, IndexError):
                continue
    except Exception:
        pass
    return result


def fetch_fii_live() -> dict[str, float | None]:
    """Fetch today's FII net flow from NSE live API."""
    result: dict[str, float | None] = {"fii_cash_net_cr": None, "fii_deriv_net_cr": None}
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/market-data/fii-dii-activity",
        }
        with httpx.Client(headers=headers, follow_redirects=True, timeout=8) as client:
            client.get("https://www.nseindia.com/", timeout=5)
            resp = client.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=5)
        if resp.status_code != 200:
            return result
        for row in resp.json() or []:
            cat = str(row.get("category", "")).upper()
            if "FII" not in cat and "FPI" not in cat:
                continue
            try:
                net = float(str(row.get("netValue", "0")).replace(",", ""))
            except (ValueError, TypeError):
                continue
            if "DERIV" in cat or "F&O" in cat:
                result["fii_deriv_net_cr"] = round(net, 0)
            else:
                result["fii_cash_net_cr"] = round(net, 0)
    except Exception:
        pass
    return result


# ── News RSS ───────────────────────────────────────────────────────────────

def _parse_rss_headlines(url: str, keywords: list[str], max_items: int = 20, timeout: int = 6) -> list[str]:
    """Fetch RSS feed and return filtered headlines matching any keyword."""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        titles = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is None or not title_el.text:
                continue
            t = title_el.text.strip()
            t_lower = t.lower()
            if any(kw in t_lower for kw in keywords):
                titles.append(t)
            if len(titles) >= max_items:
                break
        return titles[:5]  # top 5 matching
    except Exception:
        return []


def fetch_india_news(target_date: date) -> list[str]:
    """Top India market-moving news headlines from Economic Times RSS."""
    feeds = [
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://economictimes.indiatimes.com/news/economy/policy/rssfeeds/1052732854.cms",
    ]
    headlines = []
    for url in feeds:
        headlines.extend(_parse_rss_headlines(url, _INDIA_HIGH_IMPACT_KEYWORDS))
    # Deduplicate, keep top 5
    seen = set()
    out = []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            out.append(h)
        if len(out) >= 5:
            break
    return out


def fetch_global_news(target_date: date) -> list[str]:
    """Top global/geopolitical market-moving headlines from Reuters RSS."""
    feeds = [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/Reuters/worldNews",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ]
    headlines = []
    for url in feeds:
        headlines.extend(_parse_rss_headlines(url, _GLOBAL_HIGH_IMPACT_KEYWORDS))
    seen = set()
    out = []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            out.append(h)
        if len(out) >= 5:
            break
    return out


# ── US earnings ────────────────────────────────────────────────────────────

def fetch_us_earnings(target_date: date, window_days: int = 3) -> list[str]:
    """Return major US tickers reporting earnings within window_days of target_date."""
    upcoming = []
    try:
        import yfinance as yf
        import pandas as pd
        for ticker in _MARKET_MOVING_TICKERS:
            try:
                t = yf.Ticker(ticker)
                cal = t.calendar
                if cal is None:
                    continue
                # calendar may be dict with 'Earnings Date' key
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if ed is None:
                        continue
                    if hasattr(ed, "__iter__") and not isinstance(ed, str):
                        dates = [pd.Timestamp(d).date() for d in ed]
                    else:
                        dates = [pd.Timestamp(ed).date()]
                elif hasattr(cal, "columns"):
                    row = cal.T.get("Earnings Date")
                    if row is None:
                        continue
                    dates = [pd.Timestamp(d).date() for d in row]
                else:
                    continue
                for d in dates:
                    if abs((d - target_date).days) <= window_days:
                        upcoming.append(f"{ticker} earnings ({d})")
            except Exception:
                pass
    except ImportError:
        pass
    return upcoming


# ── Aggregator ────────────────────────────────────────────────────────────

def fetch_global_context(target_date: date) -> dict[str, Any]:
    """Aggregate all global context signals for the given NSE session date.

    Uses historical sources for past dates; live APIs only for today/yesterday.
    All fields None if the respective fetch failed — never raises.
    """
    from datetime import date as _date
    days_ago = (_date.today() - target_date).days
    is_today = days_ago <= 1

    us = fetch_us_market_close(target_date)
    gift = fetch_gift_nifty_gap(target_date)
    fii = fetch_fii_live() if is_today else fetch_fii_historical(target_date)
    events = check_event_proximity(target_date)
    india_news = fetch_india_news(target_date)
    global_news = fetch_global_news(target_date)
    earnings = fetch_us_earnings(target_date)

    return {
        **us,
        **gift,
        **fii,
        "upcoming_events": events,
        "india_news": india_news,
        "global_news": global_news,
        "us_earnings_nearby": earnings,
    }
