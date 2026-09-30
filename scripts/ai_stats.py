#!/usr/bin/env python3
"""Render public Claude Code usage cards for the profile README.

Reads local Claude Code data, keeps daily counters in a local ledger,
and writes scrubbed SVG cards to assets/ai/. No git, no network.

Usage: python3 scripts/ai_stats.py
"""
import copy
import html
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jakarta")
COUNTERS = ("tools", "mcp", "skills", "models", "hours")
WINDOW_DAYS = 90
TOP_N = 6
THEMES = {
    "dark": {"bg": "#011627", "title": "#c792ea", "text": "#7fdbca", "accent": "#ffeb95", "muted": "#5f7e97"},
    "light": {"bg": "#fffefe", "title": "#2f80ed", "text": "#434d58", "accent": "#4c71f2", "muted": "#8b949e"},
}
FONT = "Segoe UI, Ubuntu, Helvetica, Arial, sans-serif"
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def local_day_hour(ts):
    """Map an ISO timestamp or epoch milliseconds to (local date, hour) strings."""
    if isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts / 1000, TZ)
    else:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ)
    return dt.date().isoformat(), str(dt.hour)


def skill_aliases(claude_dir, project_dirs):
    """Map typed command names (full and bare) to canonical skill names."""
    aliases = {}

    def add(full):
        aliases[full] = full
        # ponytail: a bare alias can shadow a built-in command with the same name; add a built-in list if that shows up
        aliases.setdefault(full.split(":")[-1], full)

    for md in claude_dir.glob("plugins/cache/*/*/*/skills/*/SKILL.md"):
        add(f"{md.parents[3].name}:{md.parent.name}")
    for md in claude_dir.glob("plugins/cache/*/*/*/commands/*.md"):
        add(f"{md.parents[2].name}:{md.stem}")
    for base in [claude_dir, *(Path(p) / ".claude" for p in project_dirs)]:
        for md in base.glob("skills/*/SKILL.md"):
            add(md.parent.name)
        for md in base.glob("commands/*.md"):
            add(md.stem)
    return aliases


def collect(claude_dir):
    """Return ({date: counters}, skipped_lines) from transcripts and prompt history."""
    days = defaultdict(lambda: {"sessions": set(), **{k: Counter() for k in COUNTERS}})
    skipped = 0
    for path in claude_dir.glob("projects/**/*.jsonl"):
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("type") not in ("user", "assistant"):
                        continue
                    day, hour = local_day_hour(entry["timestamp"])
                except (ValueError, KeyError, TypeError, AttributeError):
                    skipped += 1
                    continue
                d = days[day]
                d["sessions"].add(entry.get("sessionId") or path.stem)
                d["hours"][hour] += 1
                if entry["type"] != "assistant":
                    continue
                msg = entry.get("message") or {}
                model = msg.get("model") or ""
                if model and not model.startswith("<"):
                    d["models"][model] += 1
                for block in msg.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name") or ""
                    if name.startswith("mcp__"):
                        d["mcp"][name.split("__")[1]] += 1
                        continue
                    d["tools"][name] += 1
                    skill = (block.get("input") or {}).get("skill") if name == "Skill" else None
                    if skill:
                        d["skills"][skill] += 1

    history = []
    history_path = claude_dir / "history.jsonl"
    if history_path.exists():
        with history_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    history.append((entry["display"], entry.get("project") or "", local_day_hour(entry["timestamp"])[0]))
                except (ValueError, KeyError, TypeError, AttributeError):
                    skipped += 1

    aliases = skill_aliases(claude_dir, {project for _, project, _ in history if project})
    for d in days.values():
        # the model calls Skill with bare or full names; count both under the full name
        resolved = Counter()
        for name, n in d["skills"].items():
            resolved[aliases.setdefault(name, name)] += n
        d["skills"] = resolved
    for text, _, day in history:
        # only days with transcripts: a typed command alone must not create an "active" day
        if not text.startswith("/") or day not in days:
            continue
        name = aliases.get(text[1:].split(" ", 1)[0])
        if name:
            days[day]["skills"][name] += 1

    return {
        day: {"sessions": len(d["sessions"]), **{k: dict(d[k]) for k in COUNTERS}}
        for day, d in days.items()
    }, skipped


def merge(old, new):
    """Per-counter max: idempotent, and deleted transcripts never lower history."""
    out = copy.deepcopy(old)
    for day, counters in new.items():
        cur = out.setdefault(day, {"sessions": 0})
        cur["sessions"] = max(cur.get("sessions", 0), counters["sessions"])
        for key in COUNTERS:
            slot = cur.setdefault(key, {})
            for name, n in counters[key].items():
                slot[name] = max(slot.get(name, 0), n)
    return out


def public_name(raw, scrub):
    """Normalize an MCP/skill/tool/model name and apply local scrub rules."""
    name = raw
    if name.startswith("claude_ai_"):
        name = name[len("claude_ai_"):].lower()
    elif name.startswith("plugin_"):
        name = name.split("_", 2)[-1]
    parts = []
    for part in name.split(":"):
        for prefix in scrub.get("strip_prefixes", []):
            if part.startswith(prefix):
                part = part[len(prefix):]
        parts.append(part)
    name = ":".join(parts)
    return scrub.get("rename", {}).get(name, name)


def shares(counter, top=TOP_N):
    """Top names as (name, percent), the rest folded into "other"."""
    total = sum(counter.values())
    if not total:
        return []
    ranked = counter.most_common()
    rows = [(name, 100 * n / total) for name, n in ranked[:top]]
    rest = sum(n for _, n in ranked[top:])
    if rest:
        rows.append(("other", 100 * rest / total))
    return rows


def longest_streak(dates):
    best = run = 0
    prev = None
    for d in sorted(map(date.fromisoformat, dates)):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


def summarize(ledger, scrub):
    """Card numbers over the 90 days ending at the last ledger date."""
    dates = sorted(ledger)
    first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    start = last - timedelta(days=WINDOW_DAYS - 1)
    window = [d for d in dates if date.fromisoformat(d) >= start]
    totals = {k: Counter() for k in COUNTERS}
    heat = [[0] * 24 for _ in range(7)]
    sessions = 0
    for d in window:
        entry = ledger[d]
        sessions += entry.get("sessions", 0)
        for key in COUNTERS:
            totals[key].update(entry.get(key, {}))
        weekday = date.fromisoformat(d).weekday()
        for hour, n in entry.get("hours", {}).items():
            heat[weekday][int(hour)] += n

    plugins = Counter()
    for raw, n in totals["skills"].items():
        if ":" in raw:
            plugins[raw.split(":", 1)[0]] += n
    for raw, n in totals["mcp"].items():
        if raw.startswith("plugin_"):
            plugins[raw.split("_", 2)[1]] += n

    def public_shares(counter, short=False):
        out = Counter()
        for raw, n in counter.items():
            name = public_name(raw, scrub)
            out[name.split(":")[-1] if short else name] += n
        return shares(out)

    span = min(WINDOW_DAYS, (last - first).days + 1)
    return {
        "first": dates[0],
        "last": dates[-1],
        "active_pct": 100 * len(window) / span,
        "streak": longest_streak(dates),
        "sessions_per_day": sessions / len(window),
        "models": public_shares(totals["models"]),
        "mcp": public_shares(totals["mcp"]),
        "skills": public_shares(totals["skills"], short=True),  # plugin shown in its own group
        "plugins": public_shares(plugins),
        "tools": public_shares(totals["tools"]),
        "heat": heat,
    }


def svg(width, height, theme, body):
    bg = THEMES[theme]["bg"]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{FONT}">'
        f'<rect width="{width}" height="{height}" rx="6" fill="{bg}"/>{"".join(body)}</svg>\n'
    )


def text(x, y, value, fill, size=14, weight=400, anchor="start"):
    return (
        f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
        f'text-anchor="{anchor}">{html.escape(str(value))}</text>'
    )


def clip(name, limit=22):
    return name if len(name) <= limit else name[: limit - 1] + "…"


def render_overview(s, theme):
    c = THEMES[theme]
    model, model_pct = s["models"][0] if s["models"] else ("none", 0)
    tiles = [
        ("Active days", f"{s['active_pct']:.0f}%"),
        ("Longest streak", f"{s['streak']} days"),
        ("Sessions / active day", f"{s['sessions_per_day']:.1f}"),
        (f"Top model · {model_pct:.0f}%", clip(model.removeprefix("claude-"), 12)),
    ]
    body = [text(24, 36, "Claude Code · how I work with AI", c["title"], 18, 600)]
    for i, (label, value) in enumerate(tiles):
        x = 24 + i * 190
        body += [text(x, 80, label, c["muted"], 12), text(x, 114, value, c["accent"], 26, 700)]
    body.append(text(24, 150, f"last 90 days · since {s['first'][:7]} · synced {s['last']}", c["muted"], 12))
    return svg(800, 170, theme, body)


def render_toolbox(s, theme):
    c = THEMES[theme]
    groups = [("MCP servers", s["mcp"]), ("Skills", s["skills"]), ("Plugins", s["plugins"]), ("Tool mix", s["tools"])]
    body = [text(24, 36, "Toolbox · share of calls, last 90 days", c["title"], 18, 600)]
    for i, (title, rows) in enumerate(groups):
        x, y = 24 + (i % 2) * 388, 72 + (i // 2) * 190
        body.append(text(x, y, title, c["title"], 14, 600))
        if not rows:
            body.append(text(x, y + 24, "none yet", c["muted"], 12))
        top = max((pct for _, pct in rows), default=1)
        for j, (name, pct) in enumerate(rows):
            ry = y + 24 + j * 22
            body += [
                text(x, ry, clip(name), c["text"], 12),
                f'<rect x="{x + 160}" y="{ry - 9}" width="{150 * pct / top:.1f}" height="10" rx="3" fill="{c["accent"]}"/>',
                text(x + 364, ry, f"{pct:.0f}%", c["text"], 12, anchor="end"),
            ]
    return svg(800, 440, theme, body)


def render_rhythm(s, theme):
    c = THEMES[theme]
    top = max(max(row) for row in s["heat"]) or 1
    body = [text(24, 36, "When I work · Asia/Jakarta, last 90 days", c["title"], 18, 600)]
    for d, row in enumerate(s["heat"]):
        y = 56 + d * 22
        body.append(text(24, y + 13, DAYS[d], c["muted"], 11))
        for h, n in enumerate(row):
            fill, alpha = (c["accent"], 0.15 + 0.85 * n / top) if n else (c["muted"], 0.12)
            body.append(
                f'<rect x="{64 + h * 29}" y="{y}" width="26" height="18" rx="3" fill="{fill}" fill-opacity="{alpha:.2f}"/>'
            )
    for h in range(0, 24, 3):
        body.append(text(64 + h * 29 + 13, 56 + 7 * 22 + 14, f"{h:02d}", c["muted"], 11, anchor="middle"))
    return svg(800, 236, theme, body)


CARDS = {"overview": render_overview, "toolbox": render_toolbox, "rhythm": render_rhythm}


def guard(paths, deny):
    """Return (path, term) for every denied term found, case-insensitive."""
    hits = []
    for path in paths:
        content = path.read_text(encoding="utf-8").lower()
        hits += [(path, term) for term in deny if term.lower() in content]
    return hits


def main(repo, home):
    scrub_path = home / ".config/ai-stats/scrub.json"
    ledger_path = home / ".local/share/ai-stats/ledger.json"
    if not scrub_path.exists():
        print(f"missing scrub config: {scrub_path}", file=sys.stderr)
        return 1
    scrub = json.loads(scrub_path.read_text(encoding="utf-8"))

    new, skipped = collect(home / ".claude")
    old = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    ledger = merge(old, new)
    if not ledger:
        print("no Claude Code data found", file=sys.stderr)
        return 1
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, indent=1, sort_keys=True), encoding="utf-8")

    s = summarize(ledger, scrub)
    out = repo / "assets/ai"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for theme in THEMES:
        for name, render in CARDS.items():
            path = out / f"{name}-{theme}.svg"
            path.write_text(render(s, theme), encoding="utf-8")
            written.append(path)

    hits = guard(written + [repo / "README.md"], scrub.get("deny", []))
    print(f"ledger  {s['first']} .. {s['last']} ({len(ledger)} days), skipped lines: {skipped}")
    for key in ("mcp", "skills", "plugins", "tools", "models"):
        print(f"{key:8}" + ", ".join(f"{name} {pct:.0f}%" for name, pct in s[key][:3]))
    for path, term in hits:
        print(f"DENY HIT: {term!r} in {path}", file=sys.stderr)
    print("guard   " + ("FAIL" if hits else "ok"))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(Path(__file__).resolve().parent.parent, Path.home()))
