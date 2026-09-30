import json
from collections import Counter
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import ai_stats


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))


def assistant(ts, session, tools=(), model="claude-opus-5-5"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": session,
        "message": {"model": model, "content": [{"type": "tool_use", "name": n, "input": i} for n, i in tools]},
    }


def epoch_ms(iso):
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


class CollectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.claude = Path(tmp.name) / ".claude"

    def test_collect(self):
        write_jsonl(self.claude / "projects/proj/s1.jsonl", [
            {"type": "user", "timestamp": "2026-09-29T20:00:00Z", "sessionId": "s1", "message": {"content": "hi"}},
            assistant("2026-09-29T20:01:00Z", "s1", [
                ("Bash", {}),
                ("mcp__github__get_me", {}),
                ("Skill", {"skill": "superpowers:brainstorming"}),
            ]),
            "{not json",
            {"type": "attachment", "timestamp": "2026-09-29T20:02:00Z"},
            assistant("2026-09-29T16:59:00Z", "s1", model="<synthetic>"),
        ])
        write_jsonl(self.claude / "projects/proj/s1/subagents/agent-a.jsonl", [
            assistant("2026-09-29T20:05:00Z", "s1", [("Read", {})]),
        ])
        skill = self.claude / "plugins/cache/mkt/caveman/1.0.0/skills/caveman/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: caveman\n---\n")
        ts = epoch_ms("2026-09-29T21:00:00")
        write_jsonl(self.claude / "history.jsonl", [
            {"display": "/caveman lite", "timestamp": ts, "project": "/nonexistent"},
            {"display": "/mcp", "timestamp": ts, "project": "/nonexistent"},
            {"display": "plain prompt", "timestamp": ts},
        ])

        days, skipped = ai_stats.collect(self.claude)

        self.assertEqual(skipped, 1)
        self.assertEqual(days["2026-09-30"], {
            "sessions": 1,
            "tools": {"Bash": 1, "Skill": 1, "Read": 1},
            "mcp": {"github": 1},
            "skills": {"superpowers:brainstorming": 1, "caveman:caveman": 1},
            "models": {"claude-opus-5-5": 2},
            "hours": {"3": 3},
        })
        # 16:59Z is 23:59 in Jakarta on the previous day; <synthetic> is not a model
        self.assertEqual(days["2026-09-29"], {
            "sessions": 1, "tools": {}, "mcp": {}, "skills": {}, "models": {}, "hours": {"23": 1},
        })

    def test_collect_without_history_file(self):
        write_jsonl(self.claude / "projects/p/s.jsonl", [assistant("2026-09-29T20:01:00Z", "s")])
        days, skipped = ai_stats.collect(self.claude)
        self.assertEqual(list(days), ["2026-09-30"])
        self.assertEqual(skipped, 0)


def day(sessions=1, **counters):
    return {"sessions": sessions, **{k: counters.get(k, {}) for k in ai_stats.COUNTERS}}


class MergeTest(unittest.TestCase):
    def test_merge_keeps_max_and_is_idempotent(self):
        old = {"2026-09-01": day(3, tools={"Bash": 10}), "2026-08-01": day(1, mcp={"github": 2})}
        # transcripts for 09-01 partly deleted: recomputed counts are lower
        new = {"2026-09-01": day(2, tools={"Bash": 4, "Read": 1}), "2026-09-02": day(1)}
        merged = ai_stats.merge(old, new)
        self.assertEqual(merged["2026-09-01"]["sessions"], 3)
        self.assertEqual(merged["2026-09-01"]["tools"], {"Bash": 10, "Read": 1})
        self.assertIn("2026-08-01", merged)
        self.assertIn("2026-09-02", merged)
        self.assertEqual(ai_stats.merge(merged, new), merged)
        self.assertEqual(old["2026-09-01"]["tools"], {"Bash": 10})  # input not mutated


class PublicNameTest(unittest.TestCase):
    scrub = {"strip_prefixes": ["acme-"], "rename": {"secret-report": "vuln-triage"}}

    def test_public_name(self):
        cases = {
            "acme-opensearch": "opensearch",
            "claude_ai_Gmail": "gmail",
            "plugin_playwright_playwright": "playwright",
            "acme-tools:acme-deploy": "tools:deploy",
            "secret-report": "vuln-triage",
            "github": "github",
        }
        for raw, expected in cases.items():
            self.assertEqual(ai_stats.public_name(raw, self.scrub), expected, raw)


class SummaryTest(unittest.TestCase):
    def test_shares_top_and_other(self):
        rows = ai_stats.shares(Counter({f"n{i}": i + 1 for i in range(8)}))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0][0], "n7")
        self.assertEqual(rows[-1][0], "other")
        self.assertAlmostEqual(sum(p for _, p in rows), 100)
        self.assertEqual(ai_stats.shares(Counter()), [])

    def test_longest_streak(self):
        self.assertEqual(ai_stats.longest_streak(["2026-09-03", "2026-09-01", "2026-09-02", "2026-09-10"]), 3)
        self.assertEqual(ai_stats.longest_streak(["2026-09-01"]), 1)

    def test_summarize_window_and_scrub(self):
        ledger = {
            "2026-01-01": day(1, mcp={"old": 100}, hours={"9": 5}),
            "2026-09-01": day(2, mcp={"github": 1}),
            "2026-09-02": day(2, mcp={"github": 1}),
            "2026-09-03": day(2, mcp={"github": 1}),
            "2026-09-30": day(
                2,
                mcp={"acme-search": 3, "plugin_playwright_playwright": 1},
                skills={"superpowers:brainstorming": 2, "caveman:caveman": 1},
                models={"claude-opus-5-5": 3, "claude-sonnet-5-5": 1},
                hours={"9": 4},
            ),
        }
        s = ai_stats.summarize(ledger, {"strip_prefixes": ["acme-"]})
        self.assertEqual((s["first"], s["last"], s["streak"]), ("2026-01-01", "2026-09-30", 3))
        self.assertAlmostEqual(s["active_pct"], 100 * 4 / 90)
        self.assertAlmostEqual(s["sessions_per_day"], 2.0)
        self.assertEqual({n for n, _ in s["mcp"]}, {"github", "search", "playwright"})
        self.assertEqual(dict(s["plugins"]).keys(), {"superpowers", "caveman", "playwright"})
        self.assertEqual(s["models"][0], ("claude-opus-5-5", 75.0))
        self.assertEqual(sum(map(sum, s["heat"])), 4)  # 2026-01-01 is outside the window
        self.assertEqual(s["heat"][date(2026, 9, 30).weekday()][9], 4)

    def test_short_history_denominator(self):
        s = ai_stats.summarize({"2026-09-21": day(), "2026-09-30": day()}, {})
        self.assertAlmostEqual(s["active_pct"], 20.0)  # 2 active of 10 days, not of 90


if __name__ == "__main__":
    unittest.main()
