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
        # yfinance's threaded download sometimes fails a ticker with "database is locked"
        # (its sqlite cache); retry the empty ones one by one, without threads.
        for s in [s for s in symbols if out[s].empty][:30]:
            try:
                out[s] = _clean(yf.download(s, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                            auto_adjust=False, progress=False, threads=False,
                                            multi_level_index=False))
            except Exception as e:
                print(f"retry {s} failed: {e}")
        return out

    def _intraday_raw(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        """5-minute bars of the last few days, index = tz-aware timestamps (exchange time)."""
        import yfinance as yf

        out: dict[str, pd.DataFrame] = {}
        symbols = sorted(set(symbols))
        if not symbols:
            return out
        raw = yf.download(symbols, period="5d", interval="5m", auto_adjust=False,
                          group_by="ticker", progress=False, threads=True, prepost=False)
        for s in symbols:
            try:
                df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "Close"])
            except (KeyError, TypeError):
                df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            out[s] = df
        return out

    def day_bars(self, symbols: list[str], day: date) -> dict[str, pd.Series]:
        """Build `day`'s daily bar (Open/High/Low/Close/Volume) from 5-minute bars.
        Used when Yahoo's daily series has no row for that day yet (it sometimes lags)."""
        out = {}
        for s, df in self._intraday_raw(symbols).items():
            if df.empty:
                continue
            days = pd.Index([ts.date() for ts in df.index])
            d = df[days == day]
            if d.empty:
                continue
            out[s] = pd.Series({"Open": float(d["Open"].iloc[0]), "High": float(d["High"].max()),
                                "Low": float(d["Low"].min()), "Close": float(d["Close"].iloc[-1]),
                                "Volume": float(d["Volume"].sum())})
        return out

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Latest traded price per symbol: {"price": float, "time": tz-aware datetime (UTC)}.
        Yahoo data is usually delayed ~15 minutes."""
        out = {}
        for s, df in self._intraday_raw(symbols).items():
            if df.empty:
                continue
            ts = df.index[-1]
            ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
            out[s] = {"price": float(df["Close"].iloc[-1]), "time": ts.to_pydatetime()}
        return out


class FakeProvider:
    """Deterministic fake market: same symbol + date → same price, always.
    Lets us test the whole system without internet."""

    def __init__(self, drift: float = 0.0003, vol: float = 0.02, missing: set[str] | None = None,
                 lagging: set[str] | None = None, quote_day: date | None = None):
        self.drift, self.vol = drift, vol
        self.missing = missing or set()
        self.lagging = lagging or set()     # daily series lacks the last day (like Yahoo sometimes)
        self.quote_day = quote_day

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

    def history(self, symbols, start, end):
        out = {}
        for s in set(symbols):
            if s in self.missing or s in self.lagging:
                df = self._series(s, end) if s in self.lagging else None
                out[s] = _clean(None) if df is None else df[(df.index >= start) & (df.index < end)]
                continue
            df = self._series(s, end)
            out[s] = df[(df.index >= start) & (df.index <= end)]
        return out

    def day_bars(self, symbols, day):
        out = {}
        for s in set(symbols):
            if s in self.missing:
                continue
            df = self._series(s, day)
            if day in df.index:
                out[s] = df.loc[day]
        return out

    def quotes(self, symbols):
        from datetime import datetime, timezone
        out = {}
        for s in set(symbols):
            if s in self.missing:
                continue
            df = self._series(s, self.quote_day or date.today())
            out[s] = {"price": float(df["Close"].iloc[-1]),
                      "time": datetime.combine(df.index[-1], datetime.min.time(), tzinfo=timezone.utc)}
        return out


def get_provider(name: str):
    return FakeProvider() if name == "fake" else YahooProvider()
