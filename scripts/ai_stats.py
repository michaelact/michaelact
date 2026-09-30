#!/usr/bin/env python3
"""Render public Claude Code usage cards for the profile README.

Reads local Claude Code data, keeps daily counters in a local ledger,
and writes scrubbed SVG cards to assets/ai/. No git, no network.

Usage: python3 scripts/ai_stats.py
"""
import copy
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jakarta")
COUNTERS = ("tools", "mcp", "skills", "models", "hours")
WINDOW_DAYS = 90
TOP_N = 6


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
        for name in d["skills"]:
            aliases.setdefault(name, name)
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

    def public_shares(counter):
        out = Counter()
        for raw, n in counter.items():
            out[public_name(raw, scrub)] += n
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
        "skills": public_shares(totals["skills"]),
        "plugins": public_shares(plugins),
        "tools": public_shares(totals["tools"]),
        "heat": heat,
    }
