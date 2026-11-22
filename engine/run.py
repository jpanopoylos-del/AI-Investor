"""
Entry point. GitHub Actions runs this every weekday evening:

    python -m engine.run                 # real prices, today's date
    python -m engine.run --provider fake --date 2026-11-20   # offline test
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import ROOT, Engine
from .dashboard import build_dashboard
from .prices import get_provider
from .screen import run_screen


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="trading day to process (YYYY-MM-DD); default = today in New York")
    ap.add_argument("--provider", default="yahoo", choices=["yahoo", "fake"])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--skip-screen", action="store_true")
    a = ap.parse_args(argv)

    root = Path(a.root)
    today = date.fromisoformat(a.date) if a.date else datetime.now(ZoneInfo("America/New_York")).date()
    provider = get_provider(a.provider)

    print(Engine(root, provider, today).run())
    if not a.skip_screen:
        s = run_screen(root, provider, today)
        print(f"screen: {s['count']} instruments scored")
    build_dashboard(root)
    print("dashboard: docs/index.html")


if __name__ == "__main__":
    main()
