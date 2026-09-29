"""Cognicost: what your Claude Code usage would cost at API prices. Runs locally, stdlib only; sharing is opt-in."""
import argparse, getpass, json, os, posixpath, re, subprocess, sys, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

PRICES_FILE = Path(__file__).with_name("prices.json")
PAGE_FILE = Path(__file__).with_name("dashboard.html")
PRICES_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
# prices.json: model -> USD per million tokens [input, output, cache_read, cache_write_5m, cache_write_1h]


def claude_root():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def parse_distros(raw):
    """`wsl.exe -l -q` prints UTF-16, one distro per line."""
    text = raw.decode("utf-16-le", "replace").replace("\x00", "").lstrip("﻿")
    # docker-desktop is Docker's internal distro, never someone's shell
    return [d for d in (x.strip() for x in text.splitlines()) if d and not d.startswith("docker-desktop")]


def wsl_distros():
    if os.name != "nt":
        return []
    try:
        return parse_distros(subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=15).stdout)
    except (OSError, subprocess.SubprocessError):
        return []


def wsl_roots(distros, base=r"\\wsl.localhost"):
    """Claude Code run inside WSL logs to the Linux home, which Windows sees at \\\\wsl.localhost\\DISTRO\\home\\USER.
    Side effect: touching that path starts a stopped distro."""
    roots = []
    for d in distros:
        try:
            homes = [Path(base) / d / "root", *sorted((Path(base) / d / "home").glob("*"))]
            roots += [h / ".claude" / "projects" for h in homes if (h / ".claude" / "projects").is_dir()]
        except OSError:
            continue  # distro won't start or isn't reachable
    return roots


def update_prices():
    d = json.load(urllib.request.urlopen(PRICES_URL, timeout=30))
    out = {}
    for k, v in sorted(d.items()):
        if k.startswith("claude-") and "/" not in k and ":" not in k and "input_cost_per_token" in v:
            g = lambda x: round((v.get(x) or 0) * 1e6, 4)
            cw = g("cache_creation_input_token_cost")
            out[k] = [g("input_cost_per_token"), g("output_cost_per_token"), g("cache_read_input_token_cost"),
                      cw, g("cache_creation_input_token_cost_above_1hr") or cw]
    PRICES_FILE.write_text(json.dumps(out, indent=0))
    return len(out)


def price_for(model, prices):
    if model in prices:
        return prices[model]
    base = re.sub(r"-\d{8}$", "", model)
    if base in prices:
        return prices[base]
    hits = [k for k in prices if base.startswith(k)]  # e.g. claude-sonnet-5-preview -> claude-sonnet-5
    return prices[max(hits, key=len)] if hits else None


def cost(r, prices):
    p = price_for(r["model"], prices)
    if not p:
        return None
    return (r["in"] * p[0] + r["out"] * p[1] + r["cr"] * p[2] + r["cw5"] * p[3] + r["cw1"] * p[4]) / 1e6


DEBUG = re.compile(r"\b(fix|bug|bugs|error|errors|broke|broken|fail|fails|failed|failing|crash|crashes|exception|"
                   r"traceback|wrong|not working|doesn'?t work|isn'?t working)\b")
TEST = re.compile(r"\b(pytest|vitest|jest|unittest|mocha|npm (run )?test|yarn test|cargo test|go test|dotnet test)\b|test_\w+\.py")
GIT = re.compile(r"\b(git|gh) ")
EDIT, SHELL, WEB = {"Edit", "Write", "NotebookEdit", "MultiEdit"}, {"Bash", "PowerShell"}, {"WebSearch", "WebFetch"}
READ = {"Read", "Grep", "Glob"}


def classify(prompt, tools, cmds):
    """Deterministic, first match wins. A turn = one user prompt plus everything the agent did until the next prompt."""
    if DEBUG.search(prompt) and (tools & EDIT or tools & SHELL):
        return "Debugging"
    if tools & EDIT:
        return "Coding"
    if TEST.search(cmds):
        return "Testing"
    if GIT.search(cmds):
        return "Git"
    if tools & {"EnterPlanMode", "ExitPlanMode"}:
        return "Planning"
    if tools & WEB:
        return "Research"
    if tools & SHELL:
        return "Shell"
    if any(t in READ or t.startswith("mcp__code-index") for t in tools):
        return "Exploration"
    return "Other tools" if tools else "Chat"


def prompt_text(o, c):
    """Text of a real user prompt; None for tool results and injected meta records (those don't start a turn)."""
    if o.get("isMeta") or o.get("isCompactSummary"):
        return None
    if isinstance(c, str):
        return c
    if isinstance(c, list) and c and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return None


def finish_turn(turn, recs):
    cat = classify(turn["prompt"].lower()[:2000], turn["tools"], turn["cmds"].lower())
    for k in turn["keys"]:
        if k in recs:
            recs[k]["cat"] = cat


def new_turn(prompt=""):
    return {"prompt": prompt, "tools": set(), "cmds": "", "keys": set()}


def load(root):
    """Read every session log; one record per API message (Claude Code logs a message several times while streaming,
    and resumed sessions re-log old history, so dedupe by message id keeping the highest output count)."""
    recs = {}
    roots = [root] if isinstance(root, (str, Path)) else root
    for path in (p for r in roots for p in Path(r).rglob("*.jsonl")):
        turn = new_turn()
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"message"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                m = o.get("message")
                if not isinstance(m, dict):
                    continue
                c = m.get("content")
                if o.get("type") == "user":
                    p = prompt_text(o, c)
                    if p is not None:
                        finish_turn(turn, recs)
                        turn = new_turn(p)
                    continue
                if o.get("type") != "assistant":
                    continue
                for b in c if isinstance(c, list) else ():  # streamed lines each hold one block of the same message
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        turn["tools"].add(b.get("name", ""))
                        if b.get("name") in SHELL and isinstance(b.get("input"), dict):
                            turn["cmds"] += " " + str(b["input"].get("command", ""))
                u, model = m.get("usage"), m.get("model")
                if not u or not model or model.startswith("<"):  # "<synthetic>" = not a real API call
                    continue
                key = m.get("id") or o.get("uuid")
                turn["keys"].add(key)
                out = u.get("output_tokens") or 0
                if key in recs and recs[key]["out"] >= out:
                    continue
                cc = u.get("cache_creation") or {}
                cw1 = cc.get("ephemeral_1h_input_tokens") or 0
                cw_total = u.get("cache_creation_input_tokens") or 0
                try:
                    day = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00")).astimezone().date()
                except (KeyError, ValueError):
                    continue
                cwd = (o.get("cwd") or path.parent.name).replace("\\", "/")
                recs[key] = dict(model=model, day=day, out=out, session=o.get("sessionId", "?"),
                                 project=posixpath.basename(cwd.rstrip("/")) or cwd,
                                 **{"in": u.get("input_tokens") or 0, "cr": u.get("cache_read_input_tokens") or 0,
                                    "cw5": max(cw_total - cw1, 0), "cw1": cw1})
        finish_turn(turn, recs)
    return list(recs.values())


def tok(n):
    for div, s in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{s}"
    return str(n)


def group(recs, prices, by):
    """-> (rows, unpriced models). rows = [(key, [msgs, in, out, cache_read, cache_write, cost])], days oldest first,
    everything else biggest cost first."""
    keyf = {"day": lambda r: r["day"].isoformat(), "project": lambda r: r["project"], "model": lambda r: r["model"],
            "category": lambda r: r["cat"], "user": lambda r: r.get("user", "me"),
            "session": lambda r: f'{r["session"][:8]} {r["project"]}'}[by]
    g = defaultdict(lambda: [0, 0, 0, 0, 0, 0.0])
    unpriced = set()
    for r in recs:
        c = cost(r, prices)
        if c is None:
            unpriced.add(r["model"])
        a = g[keyf(r)]
        a[0] += r.get("n", 1); a[1] += r["in"]; a[2] += r["out"]; a[3] += r["cr"]; a[4] += r["cw5"] + r["cw1"]; a[5] += c or 0
    return sorted(g.items(), key=(lambda kv: kv[0]) if by == "day" else (lambda kv: -kv[1][5])), unpriced


def report(recs, prices, by):
    rows, unpriced = group(recs, prices, by)
    tot = [sum(v[i] for _, v in rows) for i in range(6)]
    head = [by, "msgs", "input", "output", "cache read", "cache write", "cost"]
    body = [[k, str(v[0]), tok(v[1]), tok(v[2]), tok(v[3]), tok(v[4]), f"${v[5]:,.2f}"] for k, v in rows]
    body.append(["TOTAL", str(tot[0]), tok(tot[1]), tok(tot[2]), tok(tot[3]), tok(tot[4]), f"${tot[5]:,.2f}"])
    w = [max(len(r[i]) for r in [head] + body) for i in range(7)]
    fmt = lambda r: "  ".join(r[0].ljust(w[0]) if i == 0 else r[i].rjust(w[i]) for i in range(7))
    lines = [fmt(head), "  ".join("-" * x for x in w)] + [fmt(r) for r in body[:-1]] + ["  ".join("-" * x for x in w), fmt(body[-1])]
    if unpriced:
        lines.append(f"\nNo price for: {', '.join(sorted(unpriced))} (counted as $0). Try --update-prices.")
    return "\n".join(lines)


def export(root, folder, name):
    """Write this machine's usage, summed per day/model/category/project, to <folder>/<name>.json. Token counts only:
    no prompts, code or file paths. Re-running overwrites the same file."""
    agg = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for r in load(root):
        a = agg[(r["day"].isoformat(), r["model"], r["cat"], r["project"])]
        for i, v in enumerate((1, r["in"], r["out"], r["cr"], r["cw5"], r["cw1"])):
            a[i] += v
    name = re.sub(r"[^\w.-]", "_", name)[:40] or "user"
    rows = [dict(zip(("day", "model", "cat", "project", "n", "in", "out", "cr", "cw5", "cw1"), (*k, *v))) for k, v in sorted(agg.items())]
    path = Path(folder) / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cognicost": 1, "user": name, "exported_at": datetime.now().isoformat(timespec="seconds"),
                                "rows": rows}, indent=1), encoding="utf-8")
    return path, len(rows), sum(r["n"] for r in rows)


def load_team(folder):
    """Read every export in a folder -> (records, [(user, exported_at)]). The folder is shared, so treat files as
    untrusted: a malformed one is skipped with a warning instead of breaking the roll-up."""
    recs, exports = [], []
    nat = lambda v: max(0, int(v))
    for p in sorted(Path(folder).glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or doc.get("cognicost") != 1:
                continue  # some other json file
            user = str(doc["user"])[:40]
            rows = [{"user": user, "day": date.fromisoformat(x["day"]), "model": str(x["model"]), "cat": str(x["cat"]),
                     "project": str(x["project"]), "session": "", "n": nat(x["n"]), "in": nat(x["in"]), "out": nat(x["out"]),
                     "cr": nat(x["cr"]), "cw5": nat(x["cw5"]), "cw1": nat(x["cw1"])} for x in doc["rows"]]
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
            print(f"Skipping {p.name}: {e!r}", file=sys.stderr)
            continue
        recs += rows
        exports.append((user, str(doc.get("exported_at", ""))))
    return recs, exports


def filter_days(recs, days):
    if not days:
        return recs
    since = date.today() - timedelta(days=days - 1)
    return [r for r in recs if r["day"] >= since]


def dashboard_data(recs, days, exports=()):
    prices = json.loads(PRICES_FILE.read_text())
    recs = filter_days(recs, days)
    day_rows, unpriced = group(recs, prices, "day")
    by_day = {k: v for k, v in day_rows}
    end = date.today()
    start = end - timedelta(days=days - 1) if days else (min(r["day"] for r in recs) if recs else end)
    daily = []
    for i in range((end - start).days + 1):  # fill idle days so gaps show as gaps
        k = (start + timedelta(days=i)).isoformat()
        v = by_day.get(k, [0, 0, 0, 0, 0, 0.0])
        daily.append({"key": k, "msgs": v[0], "out": v[2], "cost": v[5]})

    def rows(by, top=8):
        out = [{"key": k, "msgs": v[0], "out": v[2], "cost": v[5]} for k, v in group(recs, prices, by)[0]]
        if len(out) > top:  # fold the tail into Other rather than draw a long list
            rest = out[top:]
            out = out[:top] + [{"key": f"Other ({len(rest)})", "msgs": sum(x["msgs"] for x in rest),
                                "out": sum(x["out"] for x in rest), "cost": sum(x["cost"] for x in rest)}]
        return out

    t = [sum(v[i] for v in by_day.values()) for i in range(6)]
    return {"days": days, "msgs": t[0], "input": t[1], "output": t[2], "cache_read": t[3], "cache_write": t[4],
            "cost": t[5], "daily": daily, "category": rows("category", 12), "project": rows("project"),
            "model": rows("model"), "unpriced": sorted(unpriced),
            "user": rows("user", 1000) if exports else [],  # people are never folded into "Other"
            "exports": [{"user": u, "at": at} for u, at in exports]}


def serve(source, port):
    """source() -> (records, exports); called per request so the page always reflects the latest logs/exports."""
    # ponytail: re-reads everything per request; add an mtime cache if a huge history makes the page slow
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    import webbrowser

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                return self.send_error(403)  # blocks DNS-rebinding reads of your usage from a web page
            if u.path == "/":
                body, ctype = PAGE_FILE.read_bytes(), "text/html; charset=utf-8"
            elif u.path == "/api/data":
                try:
                    days = max(0, int(parse_qs(u.query).get("days", ["30"])[0]))
                except ValueError:
                    return self.send_error(400)
                recs, exports = source()
                body, ctype = json.dumps(dashboard_data(recs, days, exports)).encode(), "application/json"
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # loopback only: nothing is exposed to the network
    url = f"http://127.0.0.1:{port}/"
    print(f"Cognicost dashboard at {url}  (Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cognicost", description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="look back N days (default 30, 0 = all time)")
    ap.add_argument("--by", choices=["day", "project", "model", "category", "session", "user"], default="day",
                    help="'user' only makes sense with --team")
    ap.add_argument("--root", help="Claude projects dir (default ~/.claude/projects or $CLAUDE_CONFIG_DIR)")
    ap.add_argument("--no-wsl", action="store_true", help="skip logs from Claude Code run inside WSL (looking there starts stopped distros)")
    ap.add_argument("--export", metavar="FOLDER", help="write your usage summary to FOLDER/<name>.json for a team roll-up")
    ap.add_argument("--name", help="name shown in the roll-up for --export (default: your Windows username)")
    ap.add_argument("--team", metavar="FOLDER", help="show the combined usage of every export in FOLDER")
    ap.add_argument("--web", action="store_true", help="open a local browser dashboard instead of printing a table")
    ap.add_argument("--port", type=int, default=8787, help="port for --web (default 8787)")
    ap.add_argument("--update-prices", action="store_true", help="refresh bundled prices from LiteLLM, then exit")
    a = ap.parse_args(argv)
    if a.export and a.team:
        ap.error("--export and --team can't be combined")
    if a.update_prices:
        print(f"Wrote {update_prices()} models to {PRICES_FILE}")
        return
    if a.team:
        if not Path(a.team).is_dir():
            raise SystemExit(f"Team folder not found: {a.team}")
        source = lambda: load_team(a.team)
    else:
        if a.root:
            roots = [Path(a.root)]
        else:
            wsl = [] if a.no_wsl else wsl_roots(wsl_distros())
            for r in wsl:
                print(f"Including WSL logs: {r}", file=sys.stderr)
            roots = [r for r in [claude_root()] if r.is_dir()] + wsl
        if not roots:
            raise SystemExit(f"No Claude Code data found at {claude_root()} (or in WSL)")
        source = lambda: (load(roots), [])
    if a.export:
        path, nrows, msgs = export(roots, a.export, a.name or getpass.getuser())
        print(f"Wrote {path}\n{msgs} messages summed into {nrows} rows (day/model/category/project, token counts only).\n"
              "Open the file to see exactly what is shared. Re-run any time to refresh it.")
        return
    if a.web:
        return serve(source, a.port)
    recs, exports = source()
    recs = filter_days(recs, a.days)
    if not recs:
        raise SystemExit("No usage in that period.")
    scope = f"last {a.days} days" if a.days else "all time"
    who = f"team of {len(exports)}" if a.team else "you"
    print(f"Cognicost - Claude Code, {who}, {scope}. Cost = tokens x API list price; your invoice may differ (seat fees, taxes, negotiated rates).")
    if exports:
        oldest = min(exports, key=lambda e: e[1])
        print(f"Oldest export: {oldest[0]} on {oldest[1] or 'unknown date'}")
    print()
    print(report(recs, json.loads(PRICES_FILE.read_text()), a.by))


if __name__ == "__main__":
    main()
