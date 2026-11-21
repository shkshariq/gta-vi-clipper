#!/bin/zsh
# Run the GTA 6 clip desk.
#   ./run.sh            start the admin UI (background) and open it        -> http://127.0.0.1:8765
#   ./run.sh stop       stop the admin UI
#   ./run.sh restart    restart the admin UI (after code changes)
#   ./run.sh status     admin UI, schedule, current run
#   ./run.sh now        start one clip run right away (same as "Run now" in the UI)
#   ./run.sh log        follow the latest run log live
#   ./run.sh setup      first-time setup: tools, Python venv, desk folder
# The schedule (From/To/Every), Telegram and LLM rules are set in the admin UI.
set -u
REPO="${0:A:h}"
DESK="${CLIP_DESK_HOME:-${REPO:h}/desk}"
PORT="${DESK_PORT:-8765}"
URL="http://127.0.0.1:$PORT"
PY="$REPO/.venv/bin/python"
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:$REPO/.venv/bin:/opt/homebrew/bin:$PATH" CLIP_DESK_HOME="$DESK"

# the admin UI = whatever python admin/app.py listens on $PORT (however it was started)
ui_pid() {
  local p
  for p in $(lsof -nP -t -iTCP:$PORT -sTCP:LISTEN 2>/dev/null); do
    ps -o command= -p $p | grep -q "admin/app.py" && { echo $p; return; }
  done
}

need_setup() {
  [[ -x $PY && -f $DESK/hourly.sh ]] || { echo "Not set up yet. Run: ./run.sh setup"; exit 1; }
}

start() {
  need_setup
  if [[ -n $(ui_pid) ]]; then echo "Admin UI already running: $URL"
  elif lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then echo "Port $PORT is used by another program. Try: DESK_PORT=8766 ./run.sh"; exit 1
  else
    mkdir -p "$DESK/logs"
    nohup "$PY" "$REPO/admin/app.py" >> "$DESK/logs/admin.log" 2>&1 &
    for i in {1..20}; do curl -s -o /dev/null "$URL/login" && break; sleep 0.3; done
    if curl -s -o /dev/null "$URL/login"; then echo "Admin UI started: $URL"; else
      echo "Admin UI did not start. Last log lines:"; tail -15 "$DESK/logs/admin.log"; exit 1; fi
  fi
  [[ ${NO_OPEN:-} == 1 ]] || open "$URL"
}

stop() {
  local p=$(ui_pid)
  if [[ -n $p ]]; then kill $p; for i in {1..20}; do [[ -z $(ui_pid) ]] && break; sleep 0.2; done; echo "Admin UI stopped."
  else echo "Admin UI is not running."; fi
}

stat_() {
  [[ -n $(ui_pid) ]] && echo "Admin UI : running  $URL" || echo "Admin UI : stopped  (./run.sh)"
  if launchctl print "gui/$(id -u)/com.gtadesk.hourly" >/dev/null 2>&1; then
    "$PY" -c "import sys; sys.path.insert(0,'$REPO/scripts'); import deskdb as d; c=d.schedule_cfg(); print(f'Schedule : on   {c[\"from\"]}-{c[\"to\"]}, every {c[\"every\"]} min')"
  else echo "Schedule : off  (turn on in Settings)"; fi
  if [[ -d $DESK/state/hourly.lock ]]; then
    echo "Run      : running since $(stat -f '%Sm' -t '%H:%M' "$DESK/state/hourly.lock")  ->  $(ls -t "$DESK"/logs/run-*.log 2>/dev/null | head -1)"
  else echo "Run      : idle"; fi
}

now() {
  need_setup
  [[ -d $DESK/state/hourly.lock ]] && { echo "A run is already going. ./run.sh log to watch it."; exit 1; }
  DESK_TRIGGER=manual nohup /bin/zsh "$DESK/hourly.sh" >/dev/null 2>&1 &
  sleep 3; echo "Run started. Watch: ./run.sh log  or  $URL"
}

log() {
  local f=$(ls -t "$DESK"/logs/run-*.log 2>/dev/null | head -1)
  [[ -n $f ]] || { echo "No run logs yet."; exit 1; }
  echo "== $f (Ctrl+C to stop watching; the run keeps going)"; tail -n 40 -f "$f"
}

setup() {
  command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
  for f in python@3.12 ffmpeg-full; do brew list "$f" >/dev/null 2>&1 || brew install "$f"; done
  command -v claude >/dev/null || echo "NOTE: Claude Code CLI not found. Install it, then log in once with: claude"
  [[ -x $PY ]] || "$(brew --prefix python@3.12)/bin/python3.12" -m venv "$REPO/.venv"
  "$PY" -m pip install -q --upgrade pip && "$PY" -m pip install -q -r "$REPO/requirements.txt"
  mkdir -p "$DESK/state" "$DESK/logs"
  if [[ ! -f $DESK/hourly.sh ]]; then
    sed -e "s|^DESK=.*|DESK=\"$DESK\"|" -e "s|^REPO=.*|REPO=\"$REPO\"|" "$REPO/desk-template/hourly.sh" > "$DESK/hourly.sh"
    chmod +x "$DESK/hourly.sh"; echo "Created $DESK/hourly.sh"
  fi
  touch "$DESK/state/done.txt"
  echo "Setup done. Next: ./run.sh  -> create the admin login, then Settings: Telegram + Scheduler, and LLM rules."
}

case "${1:-start}" in
  start) start ;; stop) stop ;; restart) stop; sleep 1; start ;; status) stat_ ;;
  now|run) now ;; log|logs) log ;; setup) setup ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
