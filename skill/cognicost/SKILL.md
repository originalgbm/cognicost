---
name: cognicost
description: Open the Cognicost dashboard in the browser to show what Claude Code and Cowork usage costs (tokens x Anthropic API list prices) by day, project, model and category. Use when the user runs /cognicost, asks to see their usage or spending, or asks how much they have spent, token usage, cost per day, project or model, or which work costs the most. Windows only; reads local session logs; nothing is sent anywhere.
---

# Cognicost (lite)

Do not read the raw session logs yourself; they are huge and the script already prices and totals them.

## Default: open the dashboard

When the user runs `/cognicost` with no arguments, or asks to see their usage or spending, run:

```
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/scripts/cognicost.ps1" -Web
```

This builds the dashboard (cost per day chart, by category, project and model, with 7 / 30 / 90 day and all-time buttons) and opens it in their default browser. It prints a short summary; repeat it in a sentence or two and tell them the page is a snapshot (run `/cognicost` again to refresh). Do not paste a table.

## A specific question: print a table

For a pointed question ("what did I spend yesterday?", "which project cost the most this week?"), print a table instead:

```
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/scripts/cognicost.ps1" -Days 30 -By day
```

- `-Days N`: look back N days (default 30; `0` = all time). Pick it from the question ("this week" = 7).
- `-By`: `day`, `project`, `model`, `category` or `session`. Run it more than once if the question needs two views.

Show the table, then summarise in a sentence or two. Report numbers exactly as printed. You can also offer to open the dashboard.

## What to tell the user when it matters

- The dollar figure is an **estimate** at Anthropic API list prices. Their invoice may differ (seat fees, taxes, negotiated rates).
- **Cowork** usage appears under the project and category `Cowork`. Cowork deletes old session data, so its older usage can be missing.
- Categories are a rough guess from the tools used in each turn, not an accurate accounting. One edit makes a whole turn "Coding".
- A model shown under "No price for" is counted as $0; prices are bundled in `prices.json` in this skill's folder.
- If PowerShell refuses to run the script (execution policy set by their organization), say so plainly instead of trying workarounds.
