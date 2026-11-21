# Desk template

The working folder ("desk") lives next to this repo, e.g. `~/Workspace/personal/gta-vi/desk`, and holds the runtime
state: `state/desk.db` (Telegram config, admin login, runs, clips), `state/done.txt`, `jobs/`, `logs/`.
None of that is in git.

Setup: copy `hourly.sh` into the desk folder (edit the `DESK`/`REPO` paths at the top if yours differ), then start the
admin UI: `cd repo && CLIP_DESK_HOME=../desk .venv/bin/python admin/app.py` -> http://<host>:8765.
Telegram, the scheduler and the LLM rules are all set in the admin UI. `hourly-prompt.md` is generated each run.
