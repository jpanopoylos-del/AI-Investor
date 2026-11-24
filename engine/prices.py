"""
Price data.

The engine never trusts prices written by the AI — it always fetches its own.
Two providers share the same interface:
  • YahooProvider  – real daily bars via yfinance (used on GitHub Actions)
  • FakeProvider   – deterministic random prices (used in tests / offline)

Every provider returns, per symbol, a pandas DataFrame indexed by date
with columns: Open, High, Low, Close, Volume  (in the symbol's own currency).
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

# Exchanges quoted in EUR on Yahoo (suffix after the dot)
EUR_SUFFIXES = (".DE", ".F", ".AS", ".PA", ".MI", ".MC", ".BR", ".HE",
                ".LS", ".VI", ".IR", ".AT")
FX_SYMBOL = "EURUSD=X"  # how many USD one EUR buys


def currency_of(symbol: str) -> str | None:
    """USD for plain US tickers, EUR for euro exchanges, None = unsupported."""
    if symbol == FX_SYMBOL:
        return "FX"
    if "." not in symbol and "=" not in symbol and "^" not in symbol:
        return "USD"                      # AAPL, BRK-B …
    if symbol.upper().endswith(EUR_SUFFIXES):
        return "EUR"
    return None                           # .L (pence), .SW (CHF) … not supported


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = [d.date() if hasattr(d, "date") else d for d in pd.to_datetime(df.index)]
    df = df[~df.index.duplicated(keep="last")]
    return df.dropna(subset=["Open", "Close"])


class YahooProvider:
    """Real market data. Needs internet access to Yahoo Finance."""

    def history(self, symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        import yfinance as yf

        out: dict[str, pd.DataFrame] = {}
        symbols = sorted(set(symbols))
        if not symbols:
            return out
        raw = yf.download(symbols, start=start.isoformat(),
                          end=(end + timedelta(days=1)).isoformat(),
                          auto_adjust=False, group_by="ticker",
                          progress=False, threads=True)
        for s in symbols:
            try:
                df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                out[s] = _clean(df)
            except KeyError:
                out[s] = _clean(None)
        return out

    def latest(self, symbols: list[str], now=None) -> dict[str, dict]:
        """Most recent intraday price per symbol: {symbol: {"price", "time" (UTC ISO)}}.
        Uses 15-minute bars over the last 5 days (Yahoo quotes are ~15 min delayed)."""
        import yfinance as yf

        out: dict[str, dict] = {}
        symbols = sorted(set(symbols))
        if not symbols:
            return out
        raw = yf.download(symbols, period="5d", interval="15m", auto_adjust=False,
                          group_by="ticker", progress=False, threads=True, prepost=False)
        for s in symbols:
            try:
                df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                c = df["Close"].dropna()
            except KeyError:
                continue
            if c.empty:
                continue
            ts = pd.Timestamp(c.index[-1])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            out[s] = {"price": float(c.iloc[-1]), "time": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00")}
        return out


class FakeProvider:
    """Deterministic fake market: same symbol + date → same price, always.
    Lets us test the whole system without internet."""

    def __init__(self, drift: float = 0.0003, vol: float = 0.02, missing: set[str] | None = None):
        self.drift, self.vol = drift, vol
        self.missing = missing or set()

    def _series(self, symbol: str, end: date) -> pd.DataFrame:
        seed = int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        days = pd.bdate_range("2024-01-01", end)
        n = len(days)
        start_px = 1.1 if symbol == FX_SYMBOL else 20 + seed % 300
        vol = 0.003 if symbol == FX_SYMBOL else self.vol
        rets = rng.normal(self.drift, vol, n)
        close = start_px * np.exp(np.cumsum(rets))
        open_ = close * np.exp(rng.normal(0, vol / 3, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
        volume = rng.integers(200_000, 5_000_000, n)
        return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                             "Close": close, "Volume": volume}, index=[d.date() for d in days])

    def latest(self, symbols, now=None):
        """Last fake close nudged by a small deterministic move, stamped 'now'."""
        from datetime import datetime, timezone
        now = now or datetime.now(timezone.utc)
        out = {}
        for s in set(symbols):
            if s in self.missing:
                continue
            df = self._series(s, now.date())
            seed = int(hashlib.md5((s + now.date().isoformat()).encode()).hexdigest()[:8], 16)
            move = 1 + ((seed % 200) - 100) / 10000        # within ±1%
            out[s] = {"price": float(df["Close"].iloc[-1]) * move,
                      "time": now.strftime("%Y-%m-%dT%H:%M:%S+00:00")}
        return out

    def history(self, symbols, start, end):
        out = {}
        for s in set(symbols):
            if s in self.missing:
                out[s] = _clean(None)
                continue
            df = self._series(s, end)
            out[s] = df[(df.index >= start) & (df.index <= end)]
        return out


def get_provider(name: str):
    return FakeProvider() if name == "fake" else YahooProvider()
