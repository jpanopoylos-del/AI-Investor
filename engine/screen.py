"""
Nightly market screen — real numbers for the AI to reason about.

For every ticker in config/universe.txt we compute classic technical facts:
  ret_20d / ret_60d / ret_120d  – momentum (% change)
  vs_sma50 / vs_sma200          – how far above/below its moving averages (%)
  rsi14                         – overbought (>70) / oversold (<30)
  from_52w_high                 – % below the 1-year high
  vol_ratio                     – recent 5-day volume vs 50-day average (news/interest)
  volatility                    – annualised daily volatility (%)
and a simple momentum score used for ranking.

The AI does NOT have to follow the ranking — it's a starting point that
it combines with news and fundamentals it researches on the web.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .prices import currency_of


def rsi(close: pd.Series, n: int = 14) -> float:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def indicators(df: pd.DataFrame) -> dict | None:
    if df is None or len(df) < 130:
        return None
    c, v = df["Close"].astype(float), df["Volume"].astype(float)
    last = float(c.iloc[-1])
    pct = lambda n: round((last / float(c.iloc[-n - 1]) - 1) * 100, 2)
    sma50, sma200 = c.tail(50).mean(), c.tail(min(200, len(c))).mean()
    return {
        "close": round(last, 4),
        "ret_5d": pct(5), "ret_20d": pct(20), "ret_60d": pct(60), "ret_120d": pct(120),
        "vs_sma50": round((last / sma50 - 1) * 100, 2),
        "vs_sma200": round((last / sma200 - 1) * 100, 2),
        "rsi14": round(rsi(c), 1),
        "from_52w_high": round((last / float(c.tail(252).max()) - 1) * 100, 2),
        "vol_ratio": round(float(v.tail(5).mean() / max(v.tail(50).mean(), 1)), 2),
        "volatility": round(float(c.pct_change().tail(20).std() * np.sqrt(252) * 100), 1),
        "avg_daily_value": round(float((c * v).tail(20).mean())),
    }


def load_universe(root: Path) -> list[str]:
    lines = (root / "config" / "universe.txt").read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def screen_symbols(root: Path) -> dict[str, str]:
    """symbol → source: 'universe' (your list), 'discovered' (found by the AI), 'held'."""
    out = {s: "universe" for s in load_universe(root)}
    disc = root / "data" / "discovered.json"
    if disc.exists():
        for s in json.loads(disc.read_text()):
            out.setdefault(s, "discovered")
    pf = root / "data" / "portfolio.json"
    if pf.exists():
        for s in json.loads(pf.read_text()).get("positions", {}):
            out.setdefault(s, "held")
    return out


def run_screen(root: Path, provider, today: date) -> dict:
    sources = screen_symbols(root)
    universe = list(sources)
    bars = provider.history(universe, today - timedelta(days=400), today)
    rows = []
    for s in universe:
        ind = indicators(bars.get(s))
        if ind:
            rows.append({"symbol": s, "currency": currency_of(s), "source": sources[s], **ind})
    df = pd.DataFrame(rows)
    if df.empty:
        out = {"as_of": today.isoformat(), "count": 0, "market": {}, "top_momentum": [],
               "oversold_in_uptrend": [], "unusual_volume": [], "all": []}
    else:
        # Momentum score = average percentile rank of 20/60/120-day returns
        df["score"] = (df["ret_20d"].rank(pct=True) * 0.2 + df["ret_60d"].rank(pct=True) * 0.4
                       + df["ret_120d"].rank(pct=True) * 0.4).round(3)
        df = df.sort_values("score", ascending=False)
        up = df[df["vs_sma200"] > 0]
        market = {
            "pct_above_sma200": round(float((df["vs_sma200"] > 0).mean() * 100), 1),
            "pct_above_sma50": round(float((df["vs_sma50"] > 0).mean() * 100), 1),
            "median_ret_20d": round(float(df["ret_20d"].median()), 2),
        }
        for idx in ("VWCE.DE", "SXR8.DE", "EQQQ.DE"):
            if idx in set(df["symbol"]):
                r = df[df["symbol"] == idx].iloc[0]
                market[idx] = {"ret_20d": r["ret_20d"], "vs_sma200": r["vs_sma200"], "rsi14": r["rsi14"]}
        out = {
            "as_of": today.isoformat(),
            "count": int(len(df)),
            "market": market,
            "top_momentum": up.head(15).to_dict("records"),
            "oversold_in_uptrend": up[up["rsi14"] < 40].sort_values("rsi14").head(10).to_dict("records"),
            "unusual_volume": df[df["vol_ratio"] > 1.8].sort_values("vol_ratio", ascending=False).head(10).to_dict("records"),
            "all": df.to_dict("records"),
        }
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "screen.json").write_text(json.dumps(out, indent=1, default=float))
    return out
