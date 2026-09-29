---
name: cognicost
description: Report what Claude Code and Cowork usage costs (tokens x Anthropic API list prices) by day, project, model, category or session. Use when the user asks how much they have spent, about token usage, cost per day, project or model, or which work costs the most. Windows only; reads local session logs; nothing is sent anywhere.
---

# Cognicost (lite)

Run the bundled script. Do not read the raw session logs yourself; they are huge and the script already prices and totals them.

```
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/scripts/cognicost.ps1" -Days 30 -By day
```

- `-Days N`: look back N days (default 30; `0` = all time). Pick it from the question ("this week" = 7).
- `-By`: `day`, `project`, `model`, `category` or `session`. Pick it from the question ("which project" = `project`, "what am I spending on" = `category`). Run it more than once if the question needs two views.

It prints a plain table. Show the table, then summarise in a sentence or two (the biggest row, any trend across days). Report numbers exactly as printed.

What to tell the user when it matters:

- The dollar figure is an **estimate** at Anthropic API list prices. Their invoice may differ (seat fees, taxes, negotiated rates).
- **Cowork** usage appears under the project and category `Cowork`. Cowork deletes old session data, so its older usage can be missing.
- Categories are a rough guess from the tools used in each turn, not an accurate accounting. One edit makes a whole turn "Coding".
- A model shown under "No price for" is counted as $0; prices are bundled in `prices.json` in this skill's folder.
- If PowerShell refuses to run the script (execution policy set by their organization), say so plainly instead of trying workarounds.
