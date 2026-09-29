"""Run: python test_cognicost.py"""
import json, tempfile
from datetime import date
from pathlib import Path

from cognicost.__main__ import cost, dashboard_data, export, load, load_team, parse_distros, price_for, wsl_roots

PRICES = {"claude-sonnet-5": [2.0, 10.0, 0.2, 2.5, 4.0]}


def line(mid, out, model="claude-sonnet-5", typ="assistant"):
    return json.dumps({"type": typ, "timestamp": "2026-09-01T15:00:00Z", "sessionId": "s1", "cwd": "C:\\work\\proj",
                       "message": {"id": mid, "model": model, "usage": {
                           "input_tokens": 1000, "output_tokens": out, "cache_read_input_tokens": 10000,
                           "cache_creation_input_tokens": 3000,
                           "cache_creation": {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 2000}}}})


def test():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "a").mkdir()
        # streamed duplicate (keep the larger), a resumed copy in another file, a synthetic msg, a non-assistant line
        (Path(d) / "a" / "1.jsonl").write_text("\n".join([line("m1", 10), line("m1", 500), line("x", 5, "<synthetic>"),
                                                          line("u", 5, typ="user")]))
        (Path(d) / "a" / "2.jsonl").write_text(line("m1", 500))
        recs = load(d)
    assert len(recs) == 1, recs
    r = recs[0]
    assert (r["out"], r["cw5"], r["cw1"], r["project"]) == (500, 2000, 1000, "proj"), r
    assert r["day"] == date(2026, 9, 1) or r["day"].year == 2026  # local tz may shift the date
    # 1000*2 + 500*10 + 10000*0.2 + 2000*2.5 + 1000*4 = 18000 per-million-units
    assert abs(cost(r, PRICES) - 0.018) < 1e-9
    assert price_for("claude-sonnet-5-20260101", PRICES) and price_for("gpt-4", PRICES) is None


def test_categories():
    def a(mid, *blocks):
        o = json.loads(line(mid, 5))
        o["message"]["content"] = list(blocks)
        return json.dumps(o)
    tu = lambda name, **inp: {"type": "tool_use", "name": name, "input": inp}
    u = lambda content: json.dumps({"type": "user", "message": {"content": content}})
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "s.jsonl").write_text("\n".join([
            u("fix the crash in parser"), a("m1", tu("Read", file_path="x")), a("m1", tu("Edit")),  # -> Debugging
            u([{"type": "tool_result", "content": "ok"}]), a("m2", {"type": "text", "text": "done"}),  # same turn
            u("add a feature"), a("m3", tu("Write")),  # -> Coding
            u("run the tests"), a("m4", tu("Bash", command="python test_x.py")),  # -> Testing
            u("what does this do"), a("m5", tu("Grep")),  # -> Exploration
            u("thanks"), a("m6", {"type": "text", "text": "np"}),  # -> Chat
        ]))
        got = sorted(r["cat"] for r in load(d))
    assert got == sorted(["Debugging", "Debugging", "Coding", "Testing", "Exploration", "Chat"]), got


def test_dashboard():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "s.jsonl").write_text(line("m1", 500))
        data = dashboard_data(load(d), 0)
    assert data["msgs"] == 1 and abs(data["cost"] - 0.018) < 1e-9, data
    assert len(data["daily"]) > 1 and sum(1 for r in data["daily"] if r["msgs"]) == 1  # idle days are filled with zeros
    assert data["project"][0]["key"] == "proj" and data["unpriced"] == []


def test_team():
    with tempfile.TemporaryDirectory() as logs, tempfile.TemporaryDirectory() as share:
        (Path(logs) / "s.jsonl").write_text("\n".join([line("m1", 500), line("m2", 500)]))
        path, nrows, msgs = export(logs, share, "Sam Q/../x")  # hostile name must not escape the folder
        assert path.parent == Path(share) and path.name == "Sam_Q_.._x.json" and (nrows, msgs) == (1, 2), (path, nrows, msgs)
        # a second person, plus junk that must be skipped rather than break the roll-up
        doc = json.loads(path.read_text()); doc["user"] = "Alex"
        (Path(share) / "Alex.json").write_text(json.dumps(doc))
        (Path(share) / "broken.json").write_text("{not json")
        (Path(share) / "other.json").write_text('{"unrelated": true}')
        (Path(share) / "evil.json").write_text(json.dumps({"cognicost": 1, "user": "E", "rows": [{"day": "nope"}]}))
        (Path(share) / "neg.json").write_text(json.dumps({"cognicost": 1, "user": "N", "rows": [
            {"day": "2026-09-01", "model": "claude-sonnet-5", "cat": "Chat", "project": "p", "n": -5, "in": -1, "out": 0, "cr": 0, "cw5": 0, "cw1": 0}]}))
        recs, exports = load_team(share)
    assert sorted(u for u, _ in exports) == ["Alex", "N", "Sam_Q_.._x"], exports
    data = dashboard_data(recs, 0, exports)
    assert data["msgs"] == 4 and abs(data["cost"] - 0.072) < 1e-9, data  # two people x 2 msgs x $0.018; negatives clamped to 0
    assert sorted(r["key"] for r in data["user"]) == ["Alex", "N", "Sam_Q_.._x"] and len(data["exports"]) == 3


def test_wsl():
    raw = "Ubuntu\r\ndocker-desktop\r\ndocker-desktop-data\r\nDebian\r\n".encode("utf-16-le")  # what wsl.exe -l -q prints
    assert parse_distros(raw) == ["Ubuntu", "Debian"], parse_distros(raw)
    assert parse_distros(b"\xff\xfe" + raw) == ["Ubuntu", "Debian"]  # tolerates a BOM
    linux = json.loads(line("m9", 300)); linux["cwd"] = "/home/sam/api"
    dup = line("m1", 500)  # same message also present on the Windows side (e.g. a resumed session)
    with tempfile.TemporaryDirectory() as win, tempfile.TemporaryDirectory() as fake_wsl:
        (Path(win) / "a.jsonl").write_text(dup)
        proj = Path(fake_wsl) / "Ubuntu" / "home" / "sam" / ".claude" / "projects" / "x"
        proj.mkdir(parents=True)
        (proj / "b.jsonl").write_text("\n".join([json.dumps(linux), dup]))
        (Path(fake_wsl) / "Ubuntu" / "home" / "nobody").mkdir()  # a home with no Claude data is ignored
        (Path(fake_wsl) / "Ubuntu" / "root" / ".claude" / "projects").mkdir(parents=True)
        roots = wsl_roots(["Ubuntu", "Missing"], base=fake_wsl)  # an unreachable distro must not raise
        assert len(roots) == 2 and all(r.name == "projects" for r in roots), roots
        recs = load([Path(win), *roots])
    assert sorted((r["project"], r["out"]) for r in recs) == [("api", 300), ("proj", 500)], recs


def test_cowork():
    o = json.loads(line("c1", 200)); o["cwd"] = "/sessions/fervent-adoring-ritchie"  # Cowork's cwd is a VM path
    o["message"]["content"] = [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]  # would be Shell in Claude Code
    blank = json.loads(line("c2", 100)); del blank["cwd"]  # some Cowork records have no cwd at all
    with tempfile.TemporaryDirectory() as t:
        cw = Path(t) / "local-agent-mode-sessions" / "acct" / "org"
        (cw / "local_1" / ".claude" / "projects" / "x").mkdir(parents=True)
        (cw / "local_1" / ".claude" / "projects" / "x" / "s.jsonl").write_text("\n".join([json.dumps(o), json.dumps(blank)]))
        (cw / "agent" / "local_1").mkdir(parents=True)
        (cw / "agent" / "local_1" / "audit.jsonl").write_text(json.dumps(o))  # same message echoed in an audit log
        (Path(t) / "code").mkdir()
        (Path(t) / "code" / "a.jsonl").write_text(line("k1", 500))  # ordinary Claude Code message alongside
        recs = load([Path(t) / "code", Path(t) / "local-agent-mode-sessions"])
    got = sorted((r["project"], r["cat"], r["out"]) for r in recs)
    assert got == [("Cowork", "Cowork", 100), ("Cowork", "Cowork", 200), ("proj", "Chat", 500)], got  # audit echo counted once


def test_skill_copies_in_sync():
    here = Path(__file__).parent
    for name in ("prices.json", "dashboard.html"):
        assert (here / "cognicost" / name).read_bytes() == (here / "skill" / "cognicost" / name).read_bytes(), \
            f"skill/cognicost/{name} is a copy of cognicost/{name}: recopy it after changing the original"
    assert "<!--COGNICOST_DATA-->" in (here / "cognicost" / "dashboard.html").read_text(encoding="utf-8")  # the skill injects data here


if __name__ == "__main__":
    test_skill_copies_in_sync()
    test()
    test_cowork()
    test_wsl()
    test_categories()
    test_dashboard()
    test_team()
    print("ok")
