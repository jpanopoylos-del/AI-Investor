"""
The trading engine — the "broker" of the simulation.

Daily cycle (run once per trading day, after the US close):

  1. OPEN   – exits for gaps: if a stock opens below its stop / above target, sell at the open
  2. OPEN   – execute AI proposals written BEFORE the open (sells first, then buys)
  3. DAY    – intraday exits: stop-loss hit (Low ≤ stop) or target hit (High ≥ target)
  4. CLOSE  – value everything at the close, update benchmark, save history

Live mode (engine/live.py, hourly during market hours): steps 1–2 run as soon
as each exchange has opened, so fills show up within the hour instead of after
midnight. The fill price is the same official open, so results are identical;
the nightly run then does steps 3–4 for the day.

Why fill at the next OPEN?  The AI writes its ideas in the morning before
markets open. Filling at that day's open price is what a real order would
get. Using an earlier price would be cheating ("look-ahead bias").
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from .prices import FX_SYMBOL, currency_of

ROOT = Path(__file__).resolve().parent.parent

# A proposal created after these UTC times is too late for that day's open
# and is filled at the NEXT trading day's open instead.
OPEN_CUTOFF_UTC = {"EUR": time(7, 0), "USD": time(14, 30)}

# Regular session open in exchange-local time (used by live mode). A few minutes'
# grace so the opening auction price is published before we fill at it.
MARKET_OPEN = {"EUR": ("Europe/Berlin", time(9, 0)), "USD": ("America/New_York", time(9, 30))}
OPEN_GRACE = timedelta(minutes=5)


def market_open_passed(ccy: str, now: datetime) -> bool:
    """Has today's session for this currency's exchanges opened (plus grace) at `now`?"""
    tz, t = MARKET_OPEN.get(ccy, MARKET_OPEN["EUR"])
    local = now.astimezone(ZoneInfo(tz))
    return local.weekday() < 5 and local >= datetime.combine(local.date(), t, tzinfo=local.tzinfo) + OPEN_GRACE


# ─────────────────────────── data structures ───────────────────────────
@dataclass
class Position:
    symbol: str
    currency: str
    qty: float
    entry_price: float          # local currency
    entry_date: str
    cost_eur: float             # what we paid incl. fees
    stop_price: float
    target_price: float
    thesis: str = ""
    last_price: float = 0.0     # last close, local currency


@dataclass
class State:
    cash_eur: float
    start_date: str | None = None
    last_run_date: str | None = None
    positions: dict[str, Position] = field(default_factory=dict)
    benchmark_qty: float = 0.0
    benchmark_last: float = 0.0
    peak_equity: float = 0.0
    halted: bool = False
    last_fx: float = 1.0        # USD per EUR

    @classmethod
    def load(cls, path: Path, starting_cash: float) -> "State":
        if not path.exists():
            return cls(cash_eur=starting_cash, peak_equity=starting_cash)
        d = json.loads(path.read_text())
        d["positions"] = {k: Position(**v) for k, v in d.get("positions", {}).items()}
        return cls(**d)

    def save(self, path: Path) -> None:
        d = asdict(self)
        path.write_text(json.dumps(d, indent=2, default=str))


# ─────────────────────────── helpers ───────────────────────────
def load_rules(root: Path = ROOT) -> dict:
    return yaml.safe_load((root / "config" / "rules.yaml").read_text())


def fee_eur(notional_eur: float, ccy: str, rules: dict) -> float:
    fee = rules["fee_flat_eur"]
    if ccy != "EUR":
        fee += notional_eur * rules["fx_fee_pct"]
    return round(fee, 4)


def to_eur(amount_local: float, ccy: str, fx: float) -> float:
    return amount_local if ccy == "EUR" else amount_local / fx


def append_csv(path: Path, row: dict) -> None:
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


def parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def eligible_today(proposal_created: datetime, today: date, ccy: str) -> bool:
    """Was this proposal written before today's market open for its exchange?"""
    created_day = proposal_created.date()
    if created_day < today:
        return True
    if created_day > today:
        return False
    return proposal_created.time() < OPEN_CUTOFF_UTC.get(ccy, time(7, 0))


# ─────────────────────────── the engine ───────────────────────────
class Engine:
    def __init__(self, root: Path, provider, today: date):
        self.root = root
        self.provider = provider
        self.today = today
        self.rules = load_rules(root)
        self.data = root / "data"
        self.data.mkdir(exist_ok=True)
        self.state = State.load(self.data / "portfolio.json", self.rules["starting_cash_eur"])
        self.bars: dict[str, pd.DataFrame] = {}
        self.decisions: list[dict] = []
        self.buys_today = self._buys_logged_today()   # live mode may already have bought today
        self.seen: set[str] = set()
        self.discovery_log: list[str] = []        # every ticker the AI mentioned (proposals + watchlist)

    def _buys_logged_today(self) -> int:
        path = self.data / "trades.csv"
        if not path.exists():
            return 0
        with path.open() as f:
            return sum(1 for r in csv.DictReader(f)
                       if r.get("date") == self.today.isoformat() and r.get("side") == "BUY"
                       and r.get("reason") == "AI proposal")

    # ---- logging -------------------------------------------------------
    def log_trade(self, symbol, side, qty, px, ccy, value_eur, fee, reason, pnl=None):
        append_csv(self.data / "trades.csv", {
            "date": self.today.isoformat(), "symbol": symbol, "side": side,
            "qty": round(qty, 6), "price": round(px, 4), "currency": ccy,
            "fx_usd_per_eur": round(self.fx, 5), "value_eur": round(value_eur, 2),
            "fee_eur": round(fee, 2), "pnl_eur": "" if pnl is None else round(pnl, 2),
            "cash_after_eur": round(self.state.cash_eur, 2), "reason": reason,
        })

    def decide(self, p: dict, status: str, reason: str):
        self.decisions.append({
            "run_date": self.today.isoformat(), "proposal_date": p.get("_file_date", ""),
            "action": p.get("action", ""), "symbol": p.get("symbol", ""),
            "status": status, "reason": reason,
            "thesis": (p.get("thesis") or "")[:300],
        })

    # ---- prices --------------------------------------------------------
    def bar(self, symbol: str):
        df = self.bars.get(symbol)
        if df is None or df.empty or self.today not in df.index:
            return None
        return df.loc[self.today]

    def patch_today(self, symbols) -> None:
        """Yahoo's daily series sometimes has no row for a session yet; rebuild that
        day's bar from 5-minute data so the engine doesn't mistake it for a holiday."""
        missing = sorted(s for s in symbols if s and self.bar(s) is None)
        if not missing or not hasattr(self.provider, "day_bars"):
            return
        try:
            synth = self.provider.day_bars(missing, self.today)
        except Exception as e:                                   # network hiccup: keep the old behaviour
            print(f"intraday fallback failed: {e}")
            return
        for s, row in synth.items():
            df = self.bars.get(s)
            new = pd.DataFrame([row], index=[self.today])[["Open", "High", "Low", "Close", "Volume"]]
            self.bars[s] = new if df is None or df.empty else pd.concat([df[df.index != self.today], new]).sort_index()
        if synth:
            print(f"{self.today}: rebuilt daily bar from intraday data for {', '.join(sorted(synth))}")

    def equity_at(self, col: str) -> float:
        total = self.state.cash_eur
        for p in self.state.positions.values():
            b = self.bar(p.symbol)
            px = float(b[col]) if b is not None else p.last_price
            total += to_eur(p.qty * px, p.currency, self.fx)
        return total

    # ---- order execution ----------------------------------------------
    def sell(self, symbol: str, px: float, reason: str):
        p = self.state.positions.pop(symbol)
        value = to_eur(p.qty * px, p.currency, self.fx)
        fee = fee_eur(value, p.currency, self.rules)
        self.state.cash_eur += value - fee
        pnl = value - fee - p.cost_eur
        self.log_trade(symbol, "SELL", p.qty, px, p.currency, value, fee, reason, pnl)

    def buy(self, symbol: str, ccy: str, px: float, invest_eur: float,
            stop_pct: float, tp_pct: float, thesis: str):
        fee = fee_eur(invest_eur, ccy, self.rules)
        qty = invest_eur / to_eur(px, ccy, self.fx)       # fractional shares allowed
        self.state.cash_eur -= invest_eur + fee
        self.state.positions[symbol] = Position(
            symbol=symbol, currency=ccy, qty=qty, entry_price=px,
            entry_date=self.today.isoformat(), cost_eur=invest_eur + fee,
            stop_price=px * (1 - stop_pct), target_price=px * (1 + tp_pct),
            thesis=thesis[:500], last_price=px)
        self.log_trade(symbol, "BUY", qty, px, ccy, invest_eur, fee, "AI proposal")
        self.buys_today += 1

    # ---- proposals -----------------------------------------------------
    def load_proposals(self, ready=None) -> tuple[list[dict], list[Path]]:
        """ready(p, ccy) -> bool: extra condition (live mode: has its market opened?).
        Proposals that aren't ready stay in the inbox for a later run."""
        inbox = self.root / "proposals" / "inbox"
        props, files = [], []
        for f in sorted(inbox.glob("*.json")):
            try:
                doc = json.loads(f.read_text())
            except json.JSONDecodeError as e:
                self.decisions.append({"run_date": self.today.isoformat(), "proposal_date": f.stem,
                                       "action": "", "symbol": "", "status": "REJECTED",
                                       "reason": f"invalid JSON: {e}", "thesis": ""})
                files.append(f)
                continue
            self.seen.update(str(x).strip().upper() for x in doc.get("watchlist", []) or [])
            self.seen.update(str(p.get("symbol", "")).strip().upper() for p in doc.get("proposals", []))
            created = parse_utc(doc.get("created_at")) or datetime.combine(
                date.fromisoformat(f.stem[:10]), time(0, 0), tzinfo=timezone.utc)
            pending = False
            for p in doc.get("proposals", []):
                p["_file_date"] = f.stem
                p["_created"] = created
                p["symbol"] = str(p.get("symbol", "")).strip().upper()
                p["action"] = str(p.get("action", "")).strip().upper()
                ccy = currency_of(p["symbol"]) or "EUR"
                age = (self.today - created.date()).days
                if age > self.rules["proposal_max_age_days"]:
                    self.decide(p, "REJECTED", f"expired ({age} days old)")
                elif not eligible_today(created, self.today, ccy) or (ready and not ready(p, ccy)):
                    pending = True                      # wait for tomorrow's open
                    p["_wait"] = True
                    continue
                else:
                    props.append(p)
            if pending:
                # archive the part handled today, keep only the waiting proposals in the inbox
                handled = [{k: v for k, v in p.items() if not k.startswith("_")}
                           for p in doc["proposals"] if not p.get("_wait")]
                if handled:
                    arch = self.root / "proposals" / "processed"
                    arch.mkdir(parents=True, exist_ok=True)
                    (arch / f"{f.stem}.part-{self.today}.json").write_text(
                        json.dumps({**doc, "proposals": handled}, indent=2, default=str))
                doc["proposals"] = [p for p in doc["proposals"] if p.get("_wait")]
                for p in doc["proposals"]:
                    for k in ("_file_date", "_created", "_wait"):
                        p.pop(k, None)
                f.write_text(json.dumps(doc, indent=2))
            else:
                files.append(f)
        return props, files

    def liquid_enough(self, symbol: str) -> tuple[bool, str]:
        df = self.bars.get(symbol)
        hist = df[df.index < self.today].tail(20) if df is not None else None
        if hist is None or len(hist) < 10:
            return False, "not enough price history"
        avg_val = float((hist["Close"] * hist["Volume"]).mean())
        if avg_val < self.rules["min_avg_daily_value"]:
            return False, f"illiquid (avg daily value {avg_val:,.0f})"
        if float(hist["Close"].iloc[-1]) < self.rules["min_price"]:
            return False, "price below minimum"
        return True, ""

    def process_proposals(self, props: list[dict]):
        r = self.rules
        sells = [p for p in props if p["action"] == "SELL"]
        buys = [p for p in props if p["action"] == "BUY"]
        others = [p for p in props if p["action"] not in ("BUY", "SELL")]
        for p in others:
            self.decide(p, "REJECTED", "action must be BUY or SELL")

        for p in sells:
            s = p["symbol"]
            if s not in self.state.positions:
                self.decide(p, "REJECTED", "not held")
                continue
            b = self.bar(s)
            if b is None:
                self.decide(p, "REJECTED", "no price today (market closed?)")
                continue
            self.sell(s, float(b["Open"]), "AI exit: " + (p.get("thesis") or "")[:80])
            self.decide(p, "FILLED", f"sold at open {float(b['Open']):.2f}")

        buys.sort(key=lambda p: -float(p.get("confidence", 0.5) or 0.5))
        equity_open = self.equity_at("Open")
        for p in buys:
            s = p["symbol"]
            ccy = currency_of(s)
            if ccy not in ("USD", "EUR"):
                self.decide(p, "REJECTED", "unsupported exchange/currency"); continue
            if self.state.halted:
                self.decide(p, "REJECTED", "circuit breaker: drawdown too large"); continue
            if s in self.state.positions:
                self.decide(p, "REJECTED", "already held"); continue
            if len(self.state.positions) >= r["max_positions"]:
                self.decide(p, "REJECTED", "max positions reached"); continue
            if self.buys_today >= r["max_new_buys_per_day"]:
                self.decide(p, "REJECTED", "max new buys per day reached"); continue
            b = self.bar(s)
            if b is None or float(b["Open"]) <= 0:
                self.decide(p, "REJECTED", "no price today (unknown ticker or market closed)"); continue
            ok, why = self.liquid_enough(s)
            if not ok:
                self.decide(p, "REJECTED", why); continue

            stop = float(p.get("stop_loss_pct") or r["default_stop_loss_pct"])
            stop = min(max(stop, r["min_stop_loss_pct"]), r["max_stop_loss_pct"])
            tp = float(p.get("take_profit_pct") or r["default_take_profit_pct"])
            size = float(p.get("size_pct") or r["max_position_pct"])
            size = min(max(size, 0.0), r["max_position_pct"])

            want = size * equity_open
            spendable = self.state.cash_eur - r["min_cash_pct"] * equity_open
            invest = min(want, spendable - fee_eur(want, ccy, r))
            if invest < r["min_trade_eur"]:
                self.decide(p, "REJECTED", f"not enough free cash (could invest €{invest:.0f})"); continue

            px = float(b["Open"])
            self.buy(s, ccy, px, invest, stop, tp, p.get("thesis", ""))
            note = " (size capped by rules)" if invest < want - 0.01 or float(p.get("size_pct") or 0) > r["max_position_pct"] else ""
            self.decide(p, "FILLED", f"bought €{invest:.0f} at open {px:.2f}, stop -{stop:.0%}, target +{tp:.0%}{note}")

    # ---- exits ---------------------------------------------------------
    def gap_exits(self):
        for s, p in list(self.state.positions.items()):
            if p.entry_date > self.today.isoformat():
                continue                       # catch-up of an older day: not held yet
            b = self.bar(s)
            if b is None:
                continue
            o = float(b["Open"])
            if o <= p.stop_price:
                self.sell(s, o, "stop-loss (gapped down at open)")
            elif o >= p.target_price:
                self.sell(s, o, "take-profit (gapped up at open)")

    def intraday_exits(self):
        for s, p in list(self.state.positions.items()):
            if p.entry_date > self.today.isoformat():
                continue
            b = self.bar(s)
            if b is None:
                continue
            # If both levels were touched we can't know which came first → assume the worse one.
            if float(b["Low"]) <= p.stop_price:
                self.sell(s, p.stop_price, "stop-loss")
            elif float(b["High"]) >= p.target_price:
                self.sell(s, p.target_price, "take-profit")
            else:
                held = (self.today - date.fromisoformat(p.entry_date)).days
                if held >= self.rules["max_holding_days"]:
                    self.sell(s, float(b["Close"]), f"time exit ({held} days)")

    # ---- discovery -----------------------------------------------------
    def update_discovered(self):
        """Tickers the AI found outside universe.txt are remembered in
        data/discovered.json so the nightly screen keeps scoring them.
        Only real, liquid, supported tickers get in; stale ones expire."""
        from .screen import load_universe

        path = self.data / "discovered.json"
        disc = json.loads(path.read_text()) if path.exists() else {}
        universe = set(load_universe(self.root))
        today = self.today.isoformat()
        for s in sorted(self.seen - universe - {""}):
            ok, why = (False, "unsupported exchange/currency") if currency_of(s) not in ("USD", "EUR") \
                else self.liquid_enough(s)
            if s in disc:
                disc[s]["last_seen"] = today
                disc[s]["times_seen"] += 1
            elif ok:
                disc[s] = {"first_seen": today, "last_seen": today, "times_seen": 1}
            else:
                self.discovery_log.append(f"{s}: not added ({why})")
                continue
        held = set(self.state.positions)
        limit = self.rules.get("discovery_expiry_days", 60)
        for s in list(disc):
            age = (self.today - date.fromisoformat(disc[s]["last_seen"])).days
            if age > limit and s not in held:
                del disc[s]
        cap = self.rules.get("max_discovered", 40)
        if len(disc) > cap:   # keep the most recently mentioned
            keep = sorted(disc, key=lambda k: (k in held, disc[k]["last_seen"]), reverse=True)[:cap]
            disc = {k: disc[k] for k in keep}
        path.write_text(json.dumps(dict(sorted(disc.items())), indent=2))

    # ---- main ----------------------------------------------------------
    def run(self) -> str:
        st, r = self.state, self.rules
        if st.last_run_date and date.fromisoformat(st.last_run_date) >= self.today:
            return f"already ran for {st.last_run_date}; nothing to do"

        start = self.today - timedelta(days=45)
        base = {r["benchmark_symbol"], FX_SYMBOL, *st.positions.keys()}
        self.bars = self.provider.history(list(base), start, self.today)
        self.patch_today(base)
        bench = self.bar(r["benchmark_symbol"])
        if bench is None:
            # benchmark (Xetra) has no bar → market holiday; proposals stay in the inbox
            return f"no market data for {self.today} (weekend/holiday) — skipped"
        fxb = self.bar(FX_SYMBOL)
        self.fx = float(fxb["Close"]) if fxb is not None else st.last_fx

        props, done_files = self.load_proposals()
        extra = ({p["symbol"] for p in props if p.get("symbol")} | self.seen) - base
        if extra:
            self.bars.update(self.provider.history(list(extra), start, self.today))
        self.patch_today({p["symbol"] for p in props})

        # First ever run: start the clock and buy the benchmark with the same €1000
        if st.start_date is None:
            st.start_date = self.today.isoformat()
            b_fee = r["fee_flat_eur"]
            st.benchmark_qty = (r["starting_cash_eur"] - b_fee) / float(bench["Open"])

        self.update_discovered()
        self.gap_exits()
        self.process_proposals(props)
        self.intraday_exits()

        # ---- close: value everything ----
        for p in st.positions.values():
            b = self.bar(p.symbol)
            if b is not None:
                p.last_price = float(b["Close"])
        invested = sum(to_eur(p.qty * p.last_price, p.currency, self.fx) for p in st.positions.values())
        equity = st.cash_eur + invested
        st.benchmark_last = float(bench["Close"])
        bench_val = st.benchmark_qty * st.benchmark_last
        st.peak_equity = max(st.peak_equity, equity)
        dd = 1 - equity / st.peak_equity
        if dd >= r["drawdown_halt_pct"]:
            st.halted = True
        elif st.halted and dd < r["drawdown_halt_pct"] / 2:
            st.halted = False
        st.last_fx = self.fx
        st.last_run_date = self.today.isoformat()

        append_csv(self.data / "equity.csv", {
            "date": self.today.isoformat(), "cash_eur": round(st.cash_eur, 2),
            "invested_eur": round(invested, 2), "equity_eur": round(equity, 2),
            "benchmark_eur": round(bench_val, 2), "positions": len(st.positions),
            "drawdown_pct": round(dd * 100, 2),
        })
        for d in self.decisions:
            append_csv(self.data / "decisions.csv", d)
        processed = self.root / "proposals" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        for f in done_files:
            f.rename(processed / f.name)
        st.save(self.data / "portfolio.json")
        self.write_summary(equity, invested, bench_val, dd)
        (self.data / "quotes.json").unlink(missing_ok=True)      # superseded by the closing prices
        return (f"{self.today}: equity €{equity:.2f} | benchmark €{bench_val:.2f} | "
                f"{len(st.positions)} positions | {len(self.decisions)} decisions")

    # ---- live mode -----------------------------------------------------
    def live(self, now: datetime) -> str:
        """Fill proposals whose market has already opened today, at today's official open.
        Exits on stops/targets and the closing valuation stay with the nightly run."""
        st, r = self.state, self.rules
        if st.start_date is None:
            return "live: portfolio not started yet (the nightly run starts it)"
        if st.last_run_date and date.fromisoformat(st.last_run_date) >= self.today:
            return f"live: {self.today} already closed by the nightly run"
        start = self.today - timedelta(days=45)
        base = {r["benchmark_symbol"], FX_SYMBOL, *st.positions.keys()}
        self.bars = self.provider.history(list(base), start, self.today)
        self.patch_today(base)
        # Unprocessed past sessions? Filling today first would mix up the books.
        last = date.fromisoformat(st.last_run_date)
        bdf = self.bars.get(r["benchmark_symbol"])
        have = set(bdf.index) if bdf is not None else set()
        gap = []
        d = last + timedelta(days=1)
        while d < self.today:
            if d.weekday() < 5:
                if d in have:
                    gap.append(d)
                elif hasattr(self.provider, "day_bars"):
                    try:
                        if self.provider.day_bars([r["benchmark_symbol"]], d):
                            gap.append(d)
                    except Exception:
                        pass
            d += timedelta(days=1)
        if gap:
            return f"live: sessions {', '.join(map(str, gap))} not processed yet — fills wait"
        if self.bar(r["benchmark_symbol"]) is None:
            return f"live: no session today for {r['benchmark_symbol']} (holiday or not open yet)"
        fxb = self.bar(FX_SYMBOL)
        self.fx = float(fxb["Close"]) if fxb is not None else st.last_fx

        # Which symbols do the inbox proposals need? (peek without consuming)
        want = set()
        for f in (self.root / "proposals" / "inbox").glob("*.json"):
            try:
                want |= {str(p.get("symbol", "")).strip().upper() for p in json.loads(f.read_text()).get("proposals", [])}
            except (json.JSONDecodeError, AttributeError):
                pass
        want -= base | {""}
        if want:
            self.bars.update(self.provider.history(sorted(want), start, self.today))
        self.patch_today(want | set(st.positions))

        def ready(p, ccy):
            return market_open_passed(ccy, now) and self.bar(p["symbol"]) is not None

        props, done_files = self.load_proposals(ready=ready)
        cash_before = st.cash_eur
        # gap exits for positions whose market has opened (same rule as the nightly run)
        for s, p in list(st.positions.items()):
            if not market_open_passed(p.currency, now):
                continue
            b = self.bar(s)
            if b is None:
                continue
            o = float(b["Open"])
            if o <= p.stop_price:
                self.sell(s, o, "stop-loss (gapped down at open)")
            elif o >= p.target_price:
                self.sell(s, o, "take-profit (gapped up at open)")
        self.process_proposals(props)
        for d in self.decisions:
            append_csv(self.data / "decisions.csv", d)
        processed = self.root / "proposals" / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        for f in done_files:
            f.rename(processed / f.name)
        st.save(self.data / "portfolio.json")
        if self.decisions or abs(st.cash_eur - cash_before) > 1e-9 or done_files:
            invested = sum(to_eur(p.qty * p.last_price, p.currency, self.fx) for p in st.positions.values())
            equity = st.cash_eur + invested
            bench_val = st.benchmark_qty * st.benchmark_last
            dd = 1 - equity / st.peak_equity if st.peak_equity else 0.0
            self.write_summary(equity, invested, bench_val, dd, as_of=st.last_run_date)
        filled = [d["symbol"] for d in self.decisions if d["status"] == "FILLED"]
        return (f"live {now:%H:%M}Z: {len(self.decisions)} decisions"
                + (f", filled {', '.join(filled)}" if filled else ""))

    def write_summary(self, equity, invested, bench_val, dd, as_of: str | None = None):
        """A compact snapshot the AI reads every morning before proposing."""
        st, r = self.state, self.rules
        start = r["starting_cash_eur"]
        positions = []
        for p in st.positions.values():
            val = to_eur(p.qty * p.last_price, p.currency, self.fx)
            positions.append({
                "symbol": p.symbol, "currency": p.currency, "entry_date": p.entry_date,
                "qty": round(p.qty, 6), "entry_price": round(p.entry_price, 4), "last_close": round(p.last_price, 4),
                "pnl_pct": round((p.last_price / p.entry_price - 1) * 100, 2),
                "value_eur": round(val, 2), "weight_pct": round(val / equity * 100, 1),
                "stop_price": round(p.stop_price, 4), "target_price": round(p.target_price, 4),
                "thesis": p.thesis,
            })
        free_cash = st.cash_eur - r["min_cash_pct"] * equity
        summary = {
            "as_of": as_of or self.today.isoformat(), "start_date": st.start_date,
            "equity_eur": round(equity, 2), "cash_eur": round(st.cash_eur, 2),
            "free_cash_for_new_buys_eur": round(max(free_cash, 0), 2),
            "return_pct": round((equity / start - 1) * 100, 2),
            "benchmark": r["benchmark_symbol"],
            "benchmark_return_pct": round((bench_val / start - 1) * 100, 2),
            "drawdown_from_peak_pct": round(dd * 100, 2),
            "buying_halted": st.halted,
            "open_slots": r["max_positions"] - len(st.positions),
            "positions": positions,
            "todays_decisions": self._decisions_logged_today() or self.decisions,
            "discovery_notes": self.discovery_log,
        }
        if as_of:
            summary["live_update"] = f"{self.today} (fills at today's open recorded intraday; closing prices after the nightly run)"
        (self.data / "summary.json").write_text(json.dumps(summary, indent=2))

    def _decisions_logged_today(self) -> list[dict]:
        path = self.data / "decisions.csv"
        if not path.exists():
            return []
        with path.open() as f:
            return [r for r in csv.DictReader(f) if r.get("run_date") == self.today.isoformat()]
