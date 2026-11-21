#!/bin/zsh
# Prepare the desk folder on the data volume, then run the service command.
set -e
D="${CLIP_DESK_HOME:-/data/desk}"
mkdir -p "$D/state" "$D/logs" "$D/jobs" "$CLAUDE_CONFIG_DIR"
touch "$D/state/done.txt"
# hourly.sh always comes from the image (repo = /app, desk = data volume)
sed -e "s|^DESK=.*|DESK=\"$D\"|" -e "s|^REPO=.*|REPO=\"/app\"|" /app/desk-template/hourly.sh > "$D/hourly.sh.new"
chmod +x "$D/hourly.sh.new" && mv "$D/hourly.sh.new" "$D/hourly.sh"
# a lock left by a container that was stopped mid-run would block runs for 150 min
if [[ "$*" == *scheduler.py* && -d "$D/state/hourly.lock" ]] && ! pgrep -f hourly.sh >/dev/null; then rm -rf "$D/state/hourly.lock"; fi
exec "$@"
