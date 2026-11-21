#!/usr/bin/env bash
# Run the GTA 6 clip desk.
#   ./run.sh            start the admin UI (background) and open it        -> http://127.0.0.1:8765
#   ./run.sh stop       stop the admin UI
#   ./run.sh restart    restart the admin UI (after code changes)
#   ./run.sh status     admin UI, schedule, current run
#   ./run.sh now        start one clip run right away (same as "Run now" in the UI)
#   ./run.sh log        follow the latest run log live
#   ./run.sh setup      first-time setup: tools, Python venv, desk folder
#
# Docker (server): everything runs in containers, data in ./data
#   ./run.sh docker up       build + start admin UI and scheduler     ./run.sh docker login   log in to Claude once
#   ./run.sh docker status   containers, schedule, current run        ./run.sh docker now     start one run
#   ./run.sh docker log      follow the latest run log                ./run.sh docker logs    container logs
#   ./run.sh docker restart  rebuild + restart (after git pull)       ./run.sh docker down    stop everything
#   ./run.sh docker shell    shell inside the admin container
# The schedule (From/To/Every), Telegram and LLM rules are set in the admin UI.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESK="${CLIP_DESK_HOME:-$(dirname "$REPO")/desk}"
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
  [[ ${NO_OPEN:-} == 1 || $(uname) != Darwin ]] || open "$URL"
}

stop() {
  local p=$(ui_pid)
  if [[ -n $p ]]; then kill $p; for i in {1..20}; do [[ -z $(ui_pid) ]] && break; sleep 0.2; done; echo "Admin UI stopped."
  else echo "Admin UI is not running."; fi
}

stat_() {
  [[ -n $(ui_pid) ]] && echo "Admin UI : running  $URL" || echo "Admin UI : stopped  (./run.sh)"
  if launchctl print "gui/$(id -u)/com.gtadesk.hourly" >/dev/null 2>&1 || crontab -l 2>/dev/null | grep -q gtadesk-hourly; then
    "$PY" -c "import sys; sys.path.insert(0,'$REPO/scripts'); import deskdb as d; c=d.schedule_cfg(); print(f'Schedule : on   {c[\"from\"]}-{c[\"to\"]}, every {c[\"every\"]} min')"
  else echo "Schedule : off  (turn on in Settings)"; fi
  if [[ -d $DESK/state/hourly.lock ]]; then
    echo "Run      : running since $(date -r "$DESK/state/hourly.lock" +%H:%M)  ->  $(ls -t "$DESK"/logs/run-*.log 2>/dev/null | head -1)"
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
  if [[ $(uname) == Darwin ]]; then
    command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
    for f in python@3.12 ffmpeg-full; do brew list "$f" >/dev/null 2>&1 || brew install "$f"; done
    VPY="$(brew --prefix python@3.12)/bin/python3.12"
  else
    for c in zsh python3 ffmpeg fc-list lsof sqlite3 curl crontab; do command -v $c >/dev/null || {
      echo "Missing $c. Run: sudo apt install -y zsh python3 python3-venv ffmpeg fonts-noto-core lsof sqlite3 curl cron"; exit 1; }; done
    VPY=python3
  fi
  command -v claude >/dev/null || echo "NOTE: Claude Code CLI not found. Install it, then log in once with: claude"
  [[ -x $PY ]] || "$VPY" -m venv "$REPO/.venv"
  "$PY" -m pip install -q --upgrade pip && "$PY" -m pip install -q -r "$REPO/requirements.txt"
  mkdir -p "$DESK/state" "$DESK/logs"
  if [[ ! -f $DESK/hourly.sh ]]; then
    sed -e "s|^DESK=.*|DESK=\"$DESK\"|" -e "s|^REPO=.*|REPO=\"$REPO\"|" "$REPO/desk-template/hourly.sh" > "$DESK/hourly.sh"
    chmod +x "$DESK/hourly.sh"; echo "Created $DESK/hourly.sh"
  fi
  touch "$DESK/state/done.txt"
  echo "Setup done. Next: ./run.sh  -> create the admin login, then Settings: Telegram + Scheduler, and LLM rules."
}

dk() {
  command -v docker >/dev/null || { echo "Docker not found. Install: https://docs.docker.com/engine/install/"; exit 1; }
  cd "$REPO"; mkdir -p data
  export DESK_UID=$(id -u) DESK_GID=$(id -g)
  local D="$REPO/data"
  dc() { docker compose "$@"; }
  case "${1:-status}" in
    up)      dc up -d --build && echo "Admin UI: $URL (on this server; from your laptop: ssh -L $PORT:127.0.0.1:$PORT <server>)" ;;
    down)    dc down ;;
    restart) dc up -d --build --force-recreate ;;
    login)   echo "Log in with the link Claude shows, then type /exit."; dc run --rm --no-deps admin claude ;;
    shell)   dc exec admin zsh ;;
    logs)    dc logs -f --tail 50 ;;
    now)     [[ -d $D/state/hourly.lock ]] && { echo "A run is already going. ./run.sh docker log to watch it."; exit 1; }
             dc exec -d -e DESK_TRIGGER=manual admin /bin/zsh /data/desk/hourly.sh && sleep 3 && echo "Run started. ./run.sh docker log to watch." ;;
    log)     local f=$(ls -t "$D"/logs/run-*.log 2>/dev/null | head -1)
             [[ -n $f ]] || { echo "No run logs yet."; exit 1; }
             echo "== $f (Ctrl+C stops watching; the run keeps going)"; tail -n 40 -f "$f" ;;
    status)  dc ps
             dc exec -T admin python -c "import sys; sys.path.insert(0,'/app/scripts'); import deskdb as d; c=d.schedule_cfg(); print('Schedule :', 'on ' if d.get('schedule_enabled')=='1' else 'off', f\"{c['from']}-{c['to']}, every {c['every']} min\")" 2>/dev/null
             [[ -d $D/state/hourly.lock ]] && echo "Run      : running since $(date -r "$D/state/hourly.lock" +%H:%M)" || echo "Run      : idle" ;;
    *)       grep -E '^#   \./run\.sh docker' "$REPO/run.sh" | sed 's/^# //'; exit 1 ;;
  esac
}

case "${1:-start}" in
  docker) shift; dk "$@"; exit ;;
  start) start ;; stop) stop ;; restart) stop; sleep 1; start ;; status) stat_ ;;
  now|run) now ;; log|logs) log ;; setup) setup ;;
  *) grep -E '^#   \./run\.sh' "$REPO/run.sh" | sed 's/^# //'; exit 1 ;;
esac
