# Cognicost

Shows what your Claude Code usage would cost at Anthropic API list prices. Reads the session logs Claude Code already
writes to `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR`). Nothing leaves your machine unless you choose to share an export (see Team roll-up). Python 3.9+, no dependencies.

![The Cognicost dashboard showing a team's Claude Code cost per day and by person, category, project and model](docs/dashboard.png)

*The `--web` dashboard in team mode. Sample data for a made-up four-person team, not real usage.*

## Install

Needs Python 3.9 or newer on Windows. Recommended, so it gets its own isolated environment and a `cognicost` command:

    pip install pipx && pipx ensurepath        # once; then open a new terminal
    pipx install git+https://github.com/originalgbm/cognicost
    pipx upgrade cognicost                     # later, to pick up a new version

Without pipx: `pip install git+https://github.com/originalgbm/cognicost`, or clone the repo and run
`python -m cognicost` from it with no install at all.
`--update-prices` rewrites the prices file inside the installed copy, so an upgrade resets it to the bundled prices.

## Claude Code skill (no Python needed)

A lighter version for people who would rather not install Python: a [Claude Code skill](https://code.claude.com/docs/en/skills)
that runs a PowerShell script (Windows PowerShell 5.1 is enough, and it ships with Windows). You ask Claude Code
"how much have I spent this week?" (or type `/cognicost`) and it prints the table and summarises it.

    git clone https://github.com/originalgbm/cognicost
    Copy-Item -Recurse cognicost\skill\cognicost $env:USERPROFILE\.claude\skills\

It reports Claude Code **and Cowork** usage by day, project, model, category or session, with the same numbers as the
Python tool (checked against it on real logs, under both PowerShell 5.1 and 7; a by-session token cell can differ by 0.1k
from display rounding). It leaves out the dashboard, the team roll-up and WSL logs. Things to know:

- It runs from **Claude Code**. Cowork doesn't read `~/.claude/skills`, but the skill still reports Cowork's usage
  because it reads Cowork's session files from disk.
- Prices are bundled in the skill's `prices.json`. To refresh them, run `cognicost --update-prices` from the Python
  tool and copy `cognicost/prices.json` over `skill/cognicost/prices.json`.
- If your organization's PowerShell policy blocks running scripts, it won't run. That has not been tested on a
  managed machine.
- An administrator can deploy it to everyone through Claude Code's managed settings directory instead of each person
  copying it (see the skills docs; not tested here).

## Use

    cognicost                        # last 30 days, by day
    cognicost --by project --days 7
    cognicost --by model --days 0    # all time
    cognicost --web                  # browser dashboard at http://127.0.0.1:8787 (--port to change)
    cognicost --by category          # Coding, Debugging, Testing, ... (see below)
    cognicost --by session
    cognicost --update-prices        # refresh cognicost/prices.json from LiteLLM

**Team roll-up** (opt-in, nothing is shared unless you run the export yourself):

> **Before you export:** the file includes your name and your **project names**, and everyone who can read the shared
> folder can see it. Check what would be shared with `cognicost --by project --days 0`; if a project name shouldn't be
> visible to your team, don't export.

    cognicost --export \\server\share\cognicost           # each person; any shared or synced folder works
    cognicost --export <folder> --name "Folder Name"      # name shown in the roll-up (default: Windows username)
    cognicost --team <folder>                             # anyone with folder access: combined table
    cognicost --team <folder> --by user                   # per person
    cognicost --team <folder> --web                       # dashboard with a "By person" card

`--export` writes one plain JSON file per person and re-running it overwrites that file, so run it whenever you want
to refresh your numbers. It covers your whole history (the viewer picks the date range). Open it first if you want to
see exactly what leaves your machine: per day, model, category and **project name**, the message count and token
counts. No prompts, code, or file paths. Everyone who can read the folder can see everyone's file, names included.
Costs are computed by whoever views the roll-up, using their copy of `prices.json`. If someone changes their `--name`,
delete their old file or they are counted twice. The viewer shows the oldest export so stale data is visible.

**The dashboard** is one HTML file served from your own machine only (127.0.0.1, so nobody else on the network can
reach it; requests with a foreign Host header are refused). It loads no libraries and makes no network calls.
Ctrl+C in the terminal stops it.

**Categories** are a deterministic guess, no AI involved. A turn is one of your prompts plus everything Claude did until
your next prompt; the whole turn gets one label, first match wins: Debugging (prompt says fix/bug/error/crash... and
Claude edited files or ran commands) > Coding (edited files) > Testing (ran a test command) > Git > Planning >
Research (web) > Shell > Exploration (read-only) > Other tools > Chat. Rules live in `classify()`.

**WSL:** if you also run Claude Code inside WSL, its logs live in the Linux home folder. Cognicost finds them through
`\\wsl.localhost\<distro>\home\<user>\.claude` and adds them to your totals (it says which paths it used). Two things
to know: looking there starts a stopped distro, so pass `--no-wsl` to skip it, and Docker Desktop's internal distros
are ignored. Use `--root <path>` to point at one specific logs folder instead.

**Cowork:** sessions from the Claude desktop app's Cowork are read automatically from
`%APPDATA%\Claude\local-agent-mode-sessions` and appear under the project **Cowork** and the category **Cowork**
(Cowork's working folders and connector tools don't fit the coding categories, so they get their own label). Cowork
deletes old session data, so its older usage can be missing from the totals. This was checked against one machine's
Cowork data only.

**The dollar figure is an estimate:** tokens x Anthropic API list price. On a usage-based Enterprise plan tokens are
billed at API rates, so it approximates your organization's bill, but it leaves out seat fees and taxes and won't match
a contract with negotiated rates. (On a Pro/Max subscription you aren't billed per token at all, so there it only
shows how much usage you got.)
