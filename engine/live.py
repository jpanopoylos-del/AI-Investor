"""
Hourly run during market hours (GitHub Actions "Live prices + fills").

    python -m engine.live                                   # real prices, now
    python -m engine.live --provider fake --now 2026-11-20T09:30:00Z

1. Catch up any closed session the nightly run missed (same as engine.run).
2. Fill proposals whose exchange has already opened today, at today's official
   open price (EU from ~10:05 Athens, US from ~16:35 Athens).
3. Write data/quotes.json with the latest price of every held position and
   every pending BUY: P&L, distance to stop/target, live portfolio value.
4. Rebuild the dashboard.

Stops/targets during the day and the closing valuation stay with the nightly run.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import ROOT, Engine, State, load_rules, market_open_passed, to_eur
from .dashboard import build_dashboard
from .prices import FX_SYMBOL, currency_of, get_provider
from .run import days_to_process, last_completed_session

ATHENS = ZoneInfo("Europe/Athens")


def pending_buys(root: Path) -> list[dict]:
    out = []
    for f in sorted((root / "proposals" / "inbox").glob("*.json")):
        try:
            doc = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        for p in doc.get("proposals", []):
            if str(p.get("action", "")).upper() == "BUY":
                out.append({**p, "symbol": str(p.get("symbol", "")).strip().upper(), "_file": f.name})
    return out


def write_quotes(root: Path, provider, now: datetime) -> dict:
    rules = load_rules(root)
    st = State.load(root / "data" / "portfolio.json", rules["starting_cash_eur"])
    pend = [p for p in pending_buys(root) if p["symbol"] not in st.positions]
    bench = rules["benchmark_symbol"]
    symbols = sorted({*st.positions, *(p["symbol"] for p in pend), bench, FX_SYMBOL})
    try:
        q = provider.quotes(symbols)
    except Exception as e:
        print(f"quotes failed: {e}")
        q = {}
    today = now.date()
    fx = q.get(FX_SYMBOL, {}).get("price") or st.last_fx

    def stamp(sym):
        x = q.get(sym)
        if not x:
            return None, None, False
        t = x["time"]
        return x["price"], t.astimezone(ATHENS).strftime("%Y-%m-%d %H:%M"), t.date() == today

    positions, invested = [], 0.0
    for s, p in st.positions.items():
        price, t, live = stamp(s)
        if price is None:
            price, t, live = p.last_price, "last close", False
        invested += to_eur(p.qty * price, p.currency, fx)
        positions.append({
            "symbol": s, "currency": p.currency, "price": round(price, 4), "time_athens": t, "live": live,
            "entry_price": round(p.entry_price, 4), "entry_date": p.entry_date,
            "pnl_pct": round((price / p.entry_price - 1) * 100, 2),
            "value_eur": round(to_eur(p.qty * price, p.currency, fx), 2),
            "stop_price": round(p.stop_price, 4), "target_price": round(p.target_price, 4),
            "to_stop_pct": round((p.stop_price / price - 1) * 100, 2) if p.stop_price else None,
            "to_target_pct": round((p.target_price / price - 1) * 100, 2) if p.target_price else None,
            **({"cash_parking": True} if s == rules.get("cash_parking_symbol") else {}),
        })
    pending = []
    seen = set()
    for p in pend:
        if p["symbol"] in seen:
            continue
        seen.add(p["symbol"])
        price, t, live = stamp(p["symbol"])
        ccy = currency_of(p["symbol"]) or "EUR"
        row = {"symbol": p["symbol"], "currency": ccy, "proposal_file": p["_file"],
               "price": None if price is None else round(price, 4), "time_athens": t, "live": live,
               "market_open": market_open_passed(ccy, now)}
        if price is not None:
            sl = float(p.get("stop_loss_pct") or rules["default_stop_loss_pct"])
            tp = float(p.get("take_profit_pct") or rules["default_take_profit_pct"])
            row.update({"stop_if_filled_now": round(price * (1 - sl), 4),
                        "target_if_filled_now": round(price * (1 + tp), 4)})
        pending.append(row)
    bprice, bt, blive = stamp(bench)
    bench_val = st.benchmark_qty * (bprice or st.benchmark_last)
    equity = st.cash_eur + invested
    start = rules["starting_cash_eur"]
    out = {
        "updated_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_athens": now.astimezone(ATHENS).strftime("%Y-%m-%d %H:%M"),
        "note": "Yahoo prices, usually ~15 min delayed. For display only; the engine trades at the official open/close.",
        "fx_usd_per_eur": round(fx, 5),
        "cash_eur": round(st.cash_eur, 2),
        "live_equity_eur": round(equity, 2),
        "live_return_pct": round((equity / start - 1) * 100, 2),
        "benchmark": {"symbol": bench, "price": bprice, "time_athens": bt, "live": blive,
                      "value_eur": round(bench_val, 2), "return_pct": round((bench_val / start - 1) * 100, 2)},
        "positions": positions,
        "pending_buys": pending,
    }
    (root / "data" / "quotes.json").write_text(json.dumps(out, indent=2))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", help="UTC time to simulate, e.g. 2026-11-20T09:30:00Z")
    ap.add_argument("--provider", default="yahoo", choices=["yahoo", "fake"])
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)

    root = Path(a.root)
    now = (datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now
           else datetime.now(timezone.utc))
    provider = get_provider(a.provider)
    if a.provider == "fake":
        provider.quote_day = now.date()

    # 1. catch up closed sessions the nightly run missed
    for d in days_to_process(root, last_completed_session(now.astimezone(ZoneInfo("America/New_York")))):
        print(Engine(root, provider, d).run())
    # 2. fills at today's open for markets that have opened
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    if today.weekday() < 5:
        print(Engine(root, provider, today).live(now))
    # 3. live quotes  4. dashboard
    if (root / "data" / "portfolio.json").exists():
        q = write_quotes(root, provider, now)
        print(f"quotes: equity €{q['live_equity_eur']:.2f} at {q['updated_athens']} Athens")
    if (root / "data" / "summary.json").exists():
        build_dashboard(root)


if __name__ == "__main__":
    main()
