#!/usr/bin/env bash
# Regenerate and commit the AI workflow cards with cli-agent-stats.
#
#   ./setup.sh          # then review the commit and run: git push
#
# Needs cli-agent-stats on PATH and ~/.config/cli-agent-stats/config.yaml
# (private scrub rules, never committed).
set -euo pipefail

CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/cli-agent-stats/config.yaml"

cd "$(dirname "$0")"

if ! command -v cli-agent-stats >/dev/null; then
  echo "cli-agent-stats not found; install it: https://github.com/michaelact/cli-agent-stats#install" >&2
  exit 1
fi
if [ ! -f "$CONFIG" ]; then
  echo "missing $CONFIG; see https://github.com/michaelact/cli-agent-stats#config" >&2
  exit 1
fi

# Exits non-zero on a deny hit, so nothing below runs and nothing gets committed.
cli-agent-stats render --config "$CONFIG" --out assets/ai --check README.md

git add assets/ai
if git diff --cached --quiet -- assets/ai; then
  echo "cards unchanged, nothing to commit"
else
  git commit -m "chore(stats): sync ai stats" -- assets/ai
  echo "committed; review with 'git show --stat', then: git push"
fi
