"""
Entry point. GitHub Actions runs this every weekday evening:

    python -m engine.run                 # real prices, today's date
    python -m engine.run --provider fake --date 2026-11-20   # offline test
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import ROOT, Engine, State, load_rules
from .dashboard import build_dashboard
from .prices import get_provider
from .screen import run_screen


US_CLOSE_DONE = time(16, 30)      # New York time; after this the day's bars are final
MAX_CATCHUP_DAYS = 10


def last_completed_session(now_ny: datetime) -> date:
    """The latest weekday whose US session has closed.

    GitHub's cron is often delayed by hours; a run scheduled for 22:15 UTC may
    start after midnight UTC (or even NY). Using "today" then picks a day with
    no bars yet and the engine skips it. Always process the last closed day.
    """
    d = now_ny.date()
    if now_ny.time() < US_CLOSE_DONE:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def days_to_process(root: Path, target: date) -> list[date]:
    """Every weekday after the last processed day up to target (catch up missed runs)."""
    rules = load_rules(root)
    st = State.load(root / "data" / "portfolio.json", rules["starting_cash_eur"])
    if not st.last_run_date:
        return [target]
    d = date.fromisoformat(st.last_run_date) + timedelta(days=1)
    out = []
    while d <= target:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out[-MAX_CATCHUP_DAYS:]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="trading day to process (YYYY-MM-DD); default = today in New York")
    ap.add_argument("--provider", default="yahoo", choices=["yahoo", "fake"])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--skip-screen", action="store_true")
    a = ap.parse_args(argv)

    root = Path(a.root)
    provider = get_provider(a.provider)
    if a.date:
        days = [date.fromisoformat(a.date)]
    else:
        days = days_to_process(root, last_completed_session(datetime.now(ZoneInfo("America/New_York"))))
    if not days:
        print("nothing to process (already up to date)")
    for d in days:
        print(Engine(root, provider, d).run())
    today = days[-1] if days else last_completed_session(datetime.now(ZoneInfo("America/New_York")))
    if not a.skip_screen:
        s = run_screen(root, provider, today)
        print(f"screen: {s['count']} instruments scored")
    save_benchmark_history(root, provider, today)
    build_dashboard(root)
    print("dashboard: docs/index.html")


def save_benchmark_history(root: Path, provider, today: date, days: int = 100):
    """Daily closes of the benchmark for the dashboard chart (context before the start date).
    Display only: the engine never reads this file. Never fails the run."""
    import csv
    try:
        sym = load_rules(root)["benchmark_symbol"]
        df = provider.history([sym], today - timedelta(days=days), today).get(sym)
        if df is None or df.empty:
            print("benchmark history: no data, keeping old file")
            return
        out = root / "data" / "benchmark_history.csv"
        with out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            for d, c in df["Close"].items():
                w.writerow([str(d), f"{float(c):.4f}"])
        print(f"benchmark history: {len(df)} days of {sym}")
    except Exception as e:  # display-only, must never break the engine run
        print(f"benchmark history failed: {e}")


if __name__ == "__main__":
    main()
