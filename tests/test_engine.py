"""Run with:  pytest -q
Each test builds a throw-away copy of the project and simulates trading days
with the FakeProvider (no internet needed)."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from engine.core import Engine, fee_eur, to_eur
from engine.prices import FakeProvider, FX_SYMBOL

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def proj(tmp_path):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    (tmp_path / "proposals" / "inbox").mkdir(parents=True)
    return tmp_path


def propose(root, day, proposals, created="T05:30:00Z"):
    doc = {"created_at": day + created, "market_view": "test", "proposals": proposals}
    (root / "proposals" / "inbox" / f"{day}.json").write_text(json.dumps(doc))


def run(root, day, provider=None):
    e = Engine(root, provider or FakeProvider(), date.fromisoformat(day))
    msg = e.run()
    return e, msg


def summary(root):
    return json.loads((root / "data" / "summary.json").read_text())


def test_first_run_starts_with_1000_and_benchmark(proj):
    e, _ = run(proj, "2026-11-02")
    s = summary(proj)
    assert s["equity_eur"] == 1000
    assert s["start_date"] == "2026-11-02"
    assert e.state.benchmark_qty > 0


def test_buy_fills_at_open_with_fees_and_size_cap(proj):
    propose(proj, "2026-11-03", [{"action": "BUY", "symbol": "AAPL", "size_pct": 0.5, "thesis": "t"}])
    run(proj, "2026-11-02")
    e, _ = run(proj, "2026-11-03")
    assert "AAPL" in e.state.positions
    pos = e.state.positions["AAPL"]
    bar = e.bars["AAPL"].loc[date(2026, 11, 3)]
    assert pos.entry_price == pytest.approx(float(bar["Open"]))
    invested = pos.cost_eur - fee_eur(pos.cost_eur, "USD", e.rules)
    assert invested <= 0.20 * 1000 + 0.01           # capped at 20%
    assert e.decisions[0]["status"] == "FILLED"


def test_cash_and_equity_add_up(proj):
    propose(proj, "2026-11-03", [{"action": "BUY", "symbol": "MSFT", "size_pct": 0.2},
                                 {"action": "BUY", "symbol": "SAP.DE", "size_pct": 0.2}])
    run(proj, "2026-11-02")
    e, _ = run(proj, "2026-11-03")
    s = summary(proj)
    pos_val = sum(to_eur(p.qty * p.last_price, p.currency, e.fx) for p in e.state.positions.values())
    assert s["equity_eur"] == pytest.approx(e.state.cash_eur + pos_val, abs=0.02)
    # money spent = positions cost; nothing created out of thin air
    spent = sum(p.cost_eur for p in e.state.positions.values())
    assert e.state.cash_eur == pytest.approx(1000 - spent, abs=0.01)


def test_late_proposal_waits_for_next_open(proj):
    run(proj, "2026-11-02")
    propose(proj, "2026-11-03", [{"action": "BUY", "symbol": "NVDA"}], created="T15:00:00Z")  # after US open
    e, _ = run(proj, "2026-11-03")
    assert "NVDA" not in e.state.positions
    assert (proj / "proposals" / "inbox" / "2026-11-03.json").exists()
    e, _ = run(proj, "2026-11-04")
    assert "NVDA" in e.state.positions
    assert e.state.positions["NVDA"].entry_date == "2026-11-04"


def test_rejections(proj):
    run(proj, "2026-11-02")
    propose(proj, "2026-11-03", [
        {"action": "BUY", "symbol": "VOD.L"},            # unsupported currency
        {"action": "SELL", "symbol": "TSLA"},            # not held
        {"action": "HOLD", "symbol": "AAPL"},            # bad action
    ])
    e, _ = run(proj, "2026-11-03")
    statuses = {d["symbol"]: d["status"] for d in e.decisions}
    assert set(statuses.values()) == {"REJECTED"}


def test_illiquid_rejected(proj):
    run(proj, "2026-11-02")
    (proj / "config" / "rules.yaml").write_text(
        (proj / "config" / "rules.yaml").read_text().replace("min_avg_daily_value: 5000000", "min_avg_daily_value: 1000000000000000"))
    propose(proj, "2026-11-03", [{"action": "BUY", "symbol": "AAPL"}])
    e, _ = run(proj, "2026-11-03")
    assert e.decisions[0]["status"] == "REJECTED" and "illiquid" in e.decisions[0]["reason"]


class CrashAfter(FakeProvider):
    """Normal prices, then AMD drops 15% from a given day on."""
    def __init__(self, day):
        super().__init__(); self.day = day
    def history(self, symbols, start, end):
        out = super().history(symbols, start, end)
        if "AMD" in out:
            df = out["AMD"].copy()
            df.loc[df.index >= self.day, ["Open", "High", "Low", "Close"]] *= 0.85
            out["AMD"] = df
        return out


def test_stop_loss_triggers(proj):
    prov = CrashAfter(date(2026, 11, 5))
    run(proj, "2026-11-02", prov)
    propose(proj, "2026-11-03", [{"action": "BUY", "symbol": "AMD", "stop_loss_pct": 0.05}])
    e, _ = run(proj, "2026-11-03", prov)
    assert "AMD" in e.state.positions
    stop = e.state.positions["AMD"].stop_price
    run(proj, "2026-11-04", prov)
    e, _ = run(proj, "2026-11-05", prov)
    assert "AMD" not in e.state.positions
    trades = (proj / "data" / "trades.csv").read_text()
    assert "stop-loss" in trades
    sell = [l for l in trades.splitlines() if ",SELL," in l and "AMD" in l][0]
    assert float(sell.split(",")[4]) <= stop + 1e-6      # never sold above the stop on a gap down


def test_weekend_is_skipped_and_rerun_is_noop(proj):
    _, msg = run(proj, "2026-11-07")                 # Saturday
    assert "skipped" in msg
    run(proj, "2026-11-09")
    _, msg = run(proj, "2026-11-09")
    assert "already ran" in msg


def test_expired_proposal(proj):
    propose(proj, "2026-11-02", [{"action": "BUY", "symbol": "AAPL"}])
    _, _ = run(proj, "2026-11-01")                    # Sunday: skipped, inbox kept
    e, _ = run(proj, "2026-11-09")
    assert e.decisions[0]["reason"].startswith("expired")


def test_discovery_adds_liquid_outside_names_and_expires(proj):
    run(proj, "2026-11-02")
    doc = {"created_at": "2026-11-03T05:30:00Z", "market_view": "t",
           "proposals": [{"action": "BUY", "symbol": "NEWCO"}],
           "watchlist": ["AAPL", "WATCHME.DE", "VOD.L"]}
    (proj / "proposals" / "inbox" / "2026-11-03.json").write_text(json.dumps(doc))
    e, _ = run(proj, "2026-11-03")
    disc = json.loads((proj / "data" / "discovered.json").read_text())
    assert set(disc) == {"NEWCO", "WATCHME.DE"}         # AAPL is in universe, VOD.L unsupported
    assert any("VOD.L" in n for n in summary(proj)["discovery_notes"])

    from engine.screen import screen_symbols
    src = screen_symbols(proj)
    assert src["WATCHME.DE"] == "discovered" and src["AAPL"] == "universe"
    assert src.get("NEWCO") in ("discovered",)          # held names stay screened too

    # 61+ days later without mentions: dropped unless held
    e, _ = run(proj, "2027-01-06")
    disc = json.loads((proj / "data" / "discovered.json").read_text())
    assert "WATCHME.DE" not in disc
    assert "NEWCO" in disc                               # was still held at the open → kept
