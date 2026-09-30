import json
import tempfile
import unittest
from datetime import datetime, timezone
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


if __name__ == "__main__":
    unittest.main()
