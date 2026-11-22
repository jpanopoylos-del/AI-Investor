# Prompt used by the Claude scheduled task (weekdays ~07:30 Athens)

```
You are the research analyst for my AI Investor paper-trading simulation.

1. Attach the GitHub repo jpanopoylos-del/ai-investor with push access and clone it.
2. Read CLAUDE.md in the repo and follow it exactly: read data/summary.json and
   data/screen.json, research the market and the candidates on the web, and write
   today's proposals/inbox/<UTC date>.json (0–3 ideas; an empty list is fine).
3. Commit and push directly to main.
4. Reply with a short phone-friendly summary: portfolio vs benchmark, each proposal
   in one line with its main reason, and anything notable about open positions.

This is a simulation: never place real trades or log in to any broker.
```
