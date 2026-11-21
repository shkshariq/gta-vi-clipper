#!/bin/zsh
# Scheduled clip run. Started by launchd (com.gtadesk.hourly, times from the admin Settings page) or "Run now" in the admin UI.
#  1. Claude Code: new trending source -> 2 face-free Tamil clips + post packs -> Telegram (videos deleted after send)
#  2. cleanup: remaining video/audio files of every job (text files: transcript, pack, script are kept forever)
#  Every run, send, failure and delete is logged in state/desk.db (see the admin UI).
set -u
DESK="$HOME/Workspace/personal/gta-vi/desk"
REPO="$HOME/Workspace/personal/gta-vi/repo"
S="$REPO/scripts"
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:$REPO/.venv/bin:/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export CLIP_DESK_HOME="$DESK"
cd "$DESK" || exit 1
mkdir -p state logs

# scheduled starts only inside the from..to window set on the admin Settings page ("Run now" always runs)
if [[ ${DESK_TRIGGER:-schedule} == schedule ]] && ! python "$S/deskdb.py" in-window; then exit 0; fi

LOCK="state/hourly.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [[ -n $(find "$LOCK" -maxdepth 0 -mmin +150 2>/dev/null) ]]; then rm -rf "$LOCK"; mkdir "$LOCK"; else echo "previous run still going"; exit 0; fi
fi
trap 'rm -rf "$DESK/$LOCK"' EXIT

RUN_ID=$(python "$S/deskdb.py" run-start --trigger "${DESK_TRIGGER:-schedule}") || exit 1
export DESK_RUN_ID="$RUN_ID"
LOG="logs/run-$RUN_ID.log"
rm -f state/summary.txt
exec >>"$LOG" 2>&1
echo "===== $(date '+%F %T') run $RUN_ID (${DESK_TRIGGER:-schedule})"

STATUS=ok
# headless Claude kills Bash commands after 2 min by default; downloads + transcription take longer
export BASH_DEFAULT_TIMEOUT_MS=2700000 BASH_MAX_TIMEOUT_MS=3000000
# stream-json + streamlog.py = one readable log line per step, live (the admin UI tails this file)
setopt pipefail
# the prompt is built from the rules on the admin UI /rules page (stored in desk.db), fresh every run
python "$S/rules.py" render --out hourly-prompt.md || { STATUS=failed; echo "rules render failed"; }
claude -p "$(cat hourly-prompt.md)" \
  --add-dir "$REPO" --output-format stream-json --verbose \
  --allowedTools "Read" "Edit" "Write" "Bash(python:*)" "Bash(ffmpeg:*)" "Bash(ffprobe:*)" "Bash(cat:*)" "Bash(grep:*)" "Bash(head:*)" "Bash(tail:*)" "Bash(ls:*)" "Bash(echo:*)" \
  | python -u "$S/streamlog.py" \
  || { STATUS=failed; python "$S/telegram.py" notify "❌ Run $RUN_ID failed. See the admin UI."; }

grep -qi "no source" state/summary.txt 2>/dev/null && [[ $STATUS == ok ]] && STATUS=no_source

# delete leftover video/audio (sources, wavs, sent previews); unsent previews stay for "Retry" in the UI
for j in jobs/*(N/); do python "$S/telegram.py" cleanup "$j"; done
find logs -name '*.log' -mtime +30 -delete 2>/dev/null

echo "===== $(date '+%F %T') done: $STATUS"
python "$S/deskdb.py" run-end "$RUN_ID" "$STATUS" "$LOG" --summary-file state/summary.txt
