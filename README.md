# AI Investor — a €1,000 paper-trading agent

An AI analyst (Claude) scans the market every weekday morning and proposes trades.
A deterministic Python engine checks them against hard risk rules, fills them at the
next market open with realistic fees, and tracks the result against simply buying a
world ETF. Everything runs in the cloud; you watch it from your phone.

> **Simulation only.** No real money, no broker connection, not financial advice.

**Dashboard:** `https://jpanopoylos-del.github.io/AI-Investor/` (after step 3 below)

---

## How it works

```
 ┌───────────── every weekday ─────────────────────────────────────────────┐
 │                                                                          │
 │  07:xx Athens  CLAUDE (scheduled task)                                   │
 │                reads data/summary.json + data/screen.json                │
 │                searches news, earnings, macro                            │
 │                writes proposals/inbox/<date>.json  → git push            │
 │                                                                          │
 │  markets open  (EU 10:00 · US 16:30 Athens)                              │
 │                                                                          │
 │  00:15 Athens  GITHUB ACTION  (.github/workflows/daily.yml)              │
 │                1. tests                                                  │
 │                2. engine: fetch real prices (Yahoo) → stops/targets →    │
 │                   proposals filled at that day's OPEN → value at CLOSE   │
 │                3. screen: score ~120 stocks/ETFs for tomorrow's scan     │
 │                4. dashboard: rebuild docs/index.html  → git push         │
 │                                                                          │
 │  GitHub Pages serves docs/index.html → your phone                        │
 └──────────────────────────────────────────────────────────────────────────┘
```

### Why split "AI" and "engine"?
LLMs are good at reading news and connecting ideas, but bad at arithmetic and
tempted to "remember" prices. So the AI only **writes ideas**; code with real
price data does every number. The AI can't break the rules, invent a fill
price, or peek into the future.

### Why fill at the next *open*?
The AI writes before the market opens, so the earliest real price it could get
is the open. A proposal written *after* the open waits for the next day.
This removes **look-ahead bias**, the #1 reason backtests look better than reality.

### Why a benchmark?
If the agent makes +6% while a world ETF (VWCE) makes +9%, the agent *lost*.
The dashboard shows both lines from the same €1,000 start.

---

## Project map

| Path | Role |
|---|---|
| `CLAUDE.md` | The agent's playbook: research steps, output format, discipline |
| `config/rules.yaml` | Risk rules (position size, stops, fees, circuit breaker) — edit freely |
| `config/universe.txt` | Tickers the nightly screen scores |
| `engine/prices.py` | Price data (Yahoo for real, Fake for tests) |
| `engine/core.py` | The broker: validates, fills, fees, stops, accounting |
| `engine/screen.py` | Technical screen → `data/screen.json` |
| `engine/dashboard.py` | Builds `docs/index.html` |
| `engine/run.py` | Entry point the Action runs |
| `tests/` | Proves the engine's maths and rules (run before every live run) |
| `data/` | Live state: `portfolio.json`, `trades.csv`, `equity.csv`, `decisions.csv` |
| `proposals/inbox` → `processed` | AI ideas waiting → already handled |

---

## Setup (step by step)

1. **Repo**: create `ai-investor` on GitHub (public, so Pages is free).
2. **Code**: push this project.
3. **Dashboard**: repo → *Settings → Pages* → Source **Deploy from a branch** → Branch `main`, folder **/docs** → Save.
4. **Actions write access**: repo → *Settings → Actions → General* → *Workflow permissions* → **Read and write** → Save.
5. **First run**: repo → *Actions* → *Daily engine run* → **Run workflow**. Green tick ≈ 1–2 min.
   This starts the €1,000 clock, buys the benchmark, and creates the first screen.
6. **AI scan**: a Claude scheduled task runs every weekday morning (see `docs/scheduled-task-prompt.md`).
   You get a push notification with its ideas; the dashboard updates after the evening run.

## Run it yourself (optional, on your computer)

```bash
pip install -r requirements.txt
pytest -q                                   # 9 tests should pass
python -m engine.run --provider fake --date 2026-11-20   # offline, fake prices
python -m engine.run                         # real prices (needs internet)
```

## Things to experiment with (learning ideas)

- **Change a rule**: e.g. `max_positions: 8` or `default_stop_loss_pct: 0.06` in `config/rules.yaml`.
- **Change the strategy**: edit section 2 of `CLAUDE.md` (e.g. "only buy stocks above SMA200 with RSI < 45").
- **Change the universe**: add tickers to `config/universe.txt`.
- **Compare agents**: copy the repo, change only `CLAUDE.md`, run both for 3 months.
- **Ask Claude Code** in this repo: *"explain engine/core.py line by line"* or *"add a Sharpe ratio tile to the dashboard"*.

## Honest limitations

- Daily bars only: if a stock touches both stop and target the same day, the engine assumes the stop (pessimistic).
- Dividends are ignored; fractional shares are assumed (most EU neobrokers allow them).
- Yahoo data is free and occasionally late or wrong; a missing price means "skip today", never a guess.
- A few months of results is mostly luck. Judge the process, and only after 6+ months.
