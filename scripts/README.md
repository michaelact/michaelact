# AI stats sync

`scripts/ai_stats.py` builds the three "AI workflow" cards on the profile README from local Claude Code data. It runs only on this laptop. It makes no network calls and runs no git commands. You publish by committing and pushing yourself.

## Sync the cards

Run from the repo root, whenever you want the profile updated:

```bash
python3 scripts/ai_stats.py && git add assets/ai && git commit -m "chore(stats): sync ai stats" && git push
```

What each part does:

| Step | What happens |
|---|---|
| `python3 scripts/ai_stats.py` | Reads `~/.claude`, updates the local ledger, writes the 6 cards to `assets/ai/`, then runs the leak check. Exits 1 on any problem, so the rest of the command does not run. |
| `git add assets/ai` | Stages only the cards. Nothing else in the repo is touched. |
| `git commit ...` | Fails with "nothing to commit" if the cards did not change, which is harmless. |
| `git push` | Publishes. The profile shows the new cards a few minutes later because GitHub caches images. |

To check the cards before publishing, run only the first part. Then open `assets/ai/*.svg` in a browser, or run `git diff --stat`.

## What the script prints

```
ledger  2026-08-12 .. 2026-10-01 (37 days), skipped lines: 0
mcp     github 42%, google-chat 23%, grafana 11%
skills  frontend-design 10%, brainstorming 9%, ...
plugins superpowers 48%, frontend-design 12%, ponytail 10%
tools   Bash 56%, Read 19%, Edit 14%
models  claude-sonnet-5 78%, ...
guard   ok
```

- `ledger` shows the date range of all stored history, plus how many broken transcript lines were skipped.
- The middle lines show the top 3 of each card group, exactly as they will appear in public.
- `guard ok` means no denied term was found in the cards or in `README.md`.

## How often to sync

There is no schedule. Sync whenever you like, but sync at least once a year. Claude Code deletes transcripts older than `cleanupPeriodDays` (set to 365 in `~/.claude/settings.json`). Days that were synced before their transcripts were deleted stay in the ledger forever.

The cards show percentages over the last 90 days of data, ending at the last day you used Claude Code. They do not look stale when you skip a few weeks.

## Files

| Path | In git? | Purpose |
|---|---|---|
| `scripts/ai_stats.py` | yes | The script |
| `scripts/test_ai_stats.py` | yes | Tests |
| `assets/ai/{overview,toolbox,rhythm}-{dark,light}.svg` | yes | The cards the README shows |
| `~/.config/ai-stats/scrub.json` | **never** | Rules that hide private names (see below) |
| `~/.local/share/ai-stats/ledger.json` | **never** | Daily counters, the only long-term copy of the history. Back it up if you care about it. |

## Scrub config

`~/.config/ai-stats/scrub.json` decides how names appear in public. It lives outside the repo, so the rules never reveal the names they hide.

```json
{
  "strip_prefixes": ["<company>-", "<product>-"],
  "rename": {"<internal-skill-name>": "<public-name>"},
  "deny": ["<company>", "<product>"]
}
```

| Key | Effect |
|---|---|
| `strip_prefixes` | Removed from the start of MCP server and skill names. For example, `<company>-opensearch` becomes `opensearch`. |
| `rename` | Exact name replacements, applied after prefix stripping. |
| `deny` | Terms that must never be published. Matching is case-insensitive. Any match makes the script exit 1. |

Changes apply to all history on the next run, because names are scrubbed when the cards are drawn, not when data is stored.

## Troubleshooting

| Message | Fix |
|---|---|
| `missing scrub config: ...` | Create `~/.config/ai-stats/scrub.json` (see above). |
| `DENY HIT: '<term>' in <file or name>` | A private name would be published. Add a `strip_prefixes` or `rename` rule for it, then re-run. Never remove the term from `deny` to make the run pass. |
| `no Claude Code data found` | `~/.claude/projects` has no transcripts. Check that you are on the right machine or user. |
| `nothing to commit` from git | The cards did not change since the last sync. Nothing to do. |
| Push rejected (`access rights`) | Fix GitHub SSH access, then run `git pull --ff-only && git push`. |
| Ledger looks wrong after a script change | Delete `~/.local/share/ai-stats/ledger.json` and re-run. **This loses every day whose transcripts are already deleted**, so do it only when needed. |

## Tests

```bash
python3 -m unittest discover -s scripts
```

Tests use temporary fake data. They never read the real `~/.claude`.
