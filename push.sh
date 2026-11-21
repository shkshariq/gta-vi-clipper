#!/usr/bin/env bash
# Commit everything and push to github.com/shkshariq/gta-vi-clipper (ssh profile: github-personal).
#   ./push.sh "what changed"        (message optional)
# Refuses to push if a Telegram bot token or a database/log file is about to be committed.
# desk-template/hourly.sh is the source of truth for the run script (Docker copies it in on start).
set -eu
cd "$(dirname "${BASH_SOURCE[0]}")"


git remote get-url origin | grep -q "github-personal:shkshariq/gta-vi-clipper" \
  || git remote set-url origin git@github-personal:shkshariq/gta-vi-clipper.git

git add -A
if git diff --cached --quiet; then
  echo "Nothing new to commit."
else
  # secret guard: bot tokens (digits:35 chars) and runtime state must never reach GitHub
  if git diff --cached -U0 | grep -E '^\+' | grep -Eq '[0-9]{8,10}:[A-Za-z0-9_-]{35}'; then
    echo "STOP: a Telegram bot token is in the staged changes. Nothing was committed."; git reset -q; exit 1
  fi
  if git diff --cached --name-only | grep -Eq '\.db(-shm|-wal)?$|(^|/)(state|logs)/'; then
    echo "STOP: database/state/log files are staged. Nothing was committed."; git reset -q; exit 1
  fi
  git status --short
  git commit -q -m "${1:-Update $(date '+%Y-%m-%d %H:%M')}"
  git log --oneline -1
fi

git push -u origin main
