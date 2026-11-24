"""
Live(-ish) quotes for the dashboard and the morning report.

The engine books (portfolio.json, summary.json, equity.csv) only ever use
daily bars. This module adds a *read-only* layer on top: the latest intraday
price for every held position, every pending (not yet filled) BUY proposal,
the benchmark and EUR/USD, written to data/quotes.json.

Nothing here changes cash, positions, stops or fills.

    python -m engine.quotes                    # refresh quotes + rebuild dashboard
    python -m engine.quotes --provider fake    # offline test
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .core import ROOT, State, load_rules, parse_utc, to_eur
from .prices import FX_SYMBOL, currency_of, get_provider

MAX_JUMP = 0.15          # reject a quote >15% away from the engine's last close
MAX_AGE_DAYS = 4         # a "latest" quote older than this is stale


def pending_buys(root: Path, held: set[str], max_age_days: int, today: date | None = None) -> list[dict]:
    """BUY proposals still in the inbox, not yet held and not expired."""
    out, today = {}, today or datetime.now(timezone.utc).date()
    for f in sorted((root / "proposals" / "inbox").glob("*.json")):
        try:
            doc = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        created = parse_utc(doc.get("created_at"))
        if created and (today - created.date()).days > max_age_days:
            continue
        for p in doc.get("proposals", []) or []:
            s = str(p.get("symbol", "")).strip().upper()
            if str(p.get("action", "")).upper() == "BUY" and s and s not in held and currency_of(s):
                out[s] = {"symbol": s, "proposal_date": f.stem,
                          "size_pct": p.get("size_pct"),
                          "stop_loss_pct": p.get("stop_loss_pct"),
                          "take_profit_pct": p.get("take_profit_pct")}
    return list(out.values())


def _screen_closes(root: Path) -> dict[str, float]:
    p = root / "data" / "screen.json"
    if not p.exists():
        return {}
    try:
        return {r["symbol"]: float(r["close"]) for r in json.loads(p.read_text()).get("all", [])
                if r.get("close") is not None}
    except Exception:
        return {}


def _check(q: dict | None, ref: float | None, now: datetime) -> tuple[dict | None, str | None]:
    """Sanity-check a quote against the engine's reference close."""
    if not q or not q.get("price"):
        return None, "no quote"
    t = datetime.fromisoformat(q["time"])
    if (now - t) > timedelta(days=MAX_AGE_DAYS):
        return None, f"stale quote ({t:%Y-%m-%d})"
    if ref and abs(q["price"] / ref - 1) > MAX_JUMP:
        return None, f"quote {q['price']:.2f} is >{MAX_JUMP:.0%} from last close {ref:.2f}"
    return q, None


def refresh_quotes(root: Path = ROOT, provider=None, now: datetime | None = None) -> dict:
    provider = provider or get_provider("yahoo")
    now = now or datetime.now(timezone.utc)
    rules = load_rules(root)
    st = State.load(root / "data" / "portfolio.json", rules["starting_cash_eur"])
    held = set(st.positions)
    pend = pending_buys(root, held, rules.get("proposal_max_age_days", 3), now.date())
    bench = rules["benchmark_symbol"]
    symbols = sorted(held | {p["symbol"] for p in pend} | {bench, FX_SYMBOL})

    try:
        raw = provider.latest(symbols, now=now)
    except Exception as e:                       # network/proxy problems: keep the old file
        print(f"quotes: fetch failed ({e}); keeping previous quotes")
        return {}
    if not any(raw.get(s) for s in symbols if s != FX_SYMBOL):
        print("quotes: no prices returned (market data unreachable?); keeping previous quotes")
        return {}

    closes = _screen_closes(root)
    fxq, _ = _check(raw.get(FX_SYMBOL), st.last_fx or None, now)
    fx = fxq["price"] if fxq else (st.last_fx or 1.0)

    positions, invested, notes = [], 0.0, []
    for p in st.positions.values():
        q, why = _check(raw.get(p.symbol), p.last_price or None, now)
        price = q["price"] if q else p.last_price
        val = to_eur(p.qty * price, p.currency, fx)
        invested += val
        if why:
            notes.append(f"{p.symbol}: {why} — using last close")
        positions.append({
            "symbol": p.symbol, "currency": p.currency,
            "price": round(price, 4), "time": q["time"] if q else None, "live": bool(q),
            "last_close": round(p.last_price, 4), "entry_price": round(p.entry_price, 4),
            "day_change_pct": round((price / p.last_price - 1) * 100, 2) if q and p.last_price else None,
            "pnl_pct": round((price / p.entry_price - 1) * 100, 2),
            "value_eur": round(val, 2),
            "stop_price": round(p.stop_price, 4), "target_price": round(p.target_price, 4),
            "to_stop_pct": round((p.stop_price / price - 1) * 100, 2),
            "to_target_pct": round((p.target_price / price - 1) * 100, 2),
        })

    pending = []
    for pp in pend:
        s = pp["symbol"]
        q, why = _check(raw.get(s), closes.get(s), now)
        if why:
            notes.append(f"{s}: {why}")
        price = q["price"] if q else closes.get(s)
        row = {**pp, "currency": currency_of(s), "price": round(price, 4) if price else None,
               "time": q["time"] if q else None, "live": bool(q),
               "last_close": closes.get(s)}
        if price and pp.get("stop_loss_pct"):
            row["est_stop"] = round(price * (1 - pp["stop_loss_pct"]), 4)
        if price and pp.get("take_profit_pct"):
            row["est_target"] = round(price * (1 + pp["take_profit_pct"]), 4)
        pending.append(row)

    bq, _ = _check(raw.get(bench), st.benchmark_last or None, now)
    bench_px = bq["price"] if bq else st.benchmark_last
    start = rules["starting_cash_eur"]
    equity = st.cash_eur + invested
    out = {
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine_as_of": st.last_run_date,
        "note": "Indicative intraday prices for display only; the engine books use daily closes.",
        "eurusd": round(fx, 5),
        "live_equity_eur": round(equity, 2),
        "live_return_pct": round((equity / start - 1) * 100, 2),
        "benchmark": {"symbol": bench, "price": round(bench_px, 4) if bench_px else None,
                      "time": bq["time"] if bq else None,
                      "return_pct": round((st.benchmark_qty * bench_px / start - 1) * 100, 2)
                      if st.benchmark_qty and bench_px else None},
        "positions": positions,
        "pending": pending,
        "notes": notes,
    }
    (root / "data" / "quotes.json").write_text(json.dumps(out, indent=2))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="yahoo", choices=["yahoo", "fake"])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--no-dashboard", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root)
    q = refresh_quotes(root, get_provider(a.provider))
    for p in q.get("positions", []):
        print(f"{p['symbol']}: {p['price']} ({p['time'] or 'last close'}) P&L {p['pnl_pct']:+.2f}%")
    for p in q.get("pending", []):
        print(f"pending {p['symbol']}: {p['price']} ({p['time'] or 'last close'})")
    if not a.no_dashboard:
        from .dashboard import build_dashboard
        build_dashboard(root)
        print("dashboard: docs/index.html")


if __name__ == "__main__":
    main()
