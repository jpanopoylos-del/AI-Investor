"""Live quotes are display-only: they must never change the engine books."""
import json
from datetime import datetime, timezone

from engine.prices import FakeProvider
from engine.quotes import refresh_quotes

from .test_engine import proj, propose, run, summary  # noqa: F401  (fixture re-use)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def _hold_sap(proj):
    propose(proj, "2026-11-03", [{"action": "BUY", "symbol": "SAP.DE", "size_pct": 0.15,
                                  "stop_loss_pct": 0.08, "take_profit_pct": 0.2}])
    run(proj, "2026-11-02")
    run(proj, "2026-11-03")
    assert summary(proj)["positions"][0]["symbol"] == "SAP.DE"


def test_quotes_cover_positions_and_pending_without_touching_books(proj):
    _hold_sap(proj)
    propose(proj, "2026-11-04", [{"action": "BUY", "symbol": "ASML.AS", "size_pct": 0.1,
                                  "stop_loss_pct": 0.06, "take_profit_pct": 0.15}])
    before = (proj / "data" / "portfolio.json").read_text()
    q = refresh_quotes(proj, FakeProvider(), now=NOW)
    assert (proj / "data" / "portfolio.json").read_text() == before
    pos = q["positions"][0]
    assert pos["symbol"] == "SAP.DE" and pos["live"] and pos["time"].startswith("2026-11-04")
    assert abs(pos["price"] / pos["last_close"] - 1) <= 0.011
    assert pos["to_stop_pct"] < 0 < pos["to_target_pct"]
    assert [p["symbol"] for p in q["pending"]] == ["ASML.AS"]
    saved = json.loads((proj / "data" / "quotes.json").read_text())
    assert saved["updated_at"] == "2026-11-04T12:00:00Z"


def test_quotes_reject_wild_price(proj):
    _hold_sap(proj)

    class Wild(FakeProvider):
        def latest(self, symbols, now=None):
            out = super().latest(symbols, now)
            out["SAP.DE"]["price"] *= 1.5
            return out

    q = refresh_quotes(proj, Wild(), now=NOW)
    pos = q["positions"][0]
    assert not pos["live"] and pos["price"] == pos["last_close"]
    assert any("SAP.DE" in n for n in q["notes"])


def test_quotes_fetch_failure_keeps_old_file(proj):
    _hold_sap(proj)
    refresh_quotes(proj, FakeProvider(), now=NOW)
    old = (proj / "data" / "quotes.json").read_text()

    class Down(FakeProvider):
        def latest(self, symbols, now=None):
            raise ConnectionError("proxy")

    assert refresh_quotes(proj, Down(), now=NOW) == {}
    assert (proj / "data" / "quotes.json").read_text() == old
