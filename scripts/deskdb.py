"""SQLite store for the clip desk: settings (Telegram), admin login, runs, clips, events.

DB file: <CLIP_DESK_HOME>/state/desk.db (chmod 600). Used by telegram.py, hourly.sh and admin/app.py.

CLI (used by hourly.sh):
  python deskdb.py run-start                      -> prints the new run id
  python deskdb.py run-end <id> <status> <logfile> [--summary-file f]
  python deskdb.py event <kind> "<message>" [--run id] [--clip id]
  python deskdb.py import-env                     -> moves ~/.config/gta-desk/telegram.env into the DB
"""
import argparse, os, sqlite3, sys, time
from pathlib import Path

HOME = Path(os.environ.get("CLIP_DESK_HOME", Path.cwd()))
DB = HOME / "state" / "desk.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT DEFAULT 'running',
  trigger TEXT DEFAULT 'schedule', summary TEXT, log TEXT);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY, run_id INTEGER, job TEXT NOT NULL, pick INTEGER NOT NULL,
  source_url TEXT, source_title TEXT, source_channel TEXT, start_sec REAL, end_sec REAL,
  hook TEXT, transcript_ta TEXT, pack TEXT, status TEXT DEFAULT 'ready',
  telegram_message_id INTEGER, sent_at TEXT, video_deleted_at TEXT, error TEXT, created_at TEXT,
  UNIQUE(job, pick));
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, message TEXT, run_id INTEGER, clip_id INTEGER);
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    new = not DB.exists()
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    if new:
        os.chmod(DB, 0o600)
    return c


def get(key, default=None):
    with connect() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def put(key, value):
    with connect() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def event(kind, message="", run_id=None, clip_id=None):
    run_id = run_id or (int(os.environ["DESK_RUN_ID"]) if os.environ.get("DESK_RUN_ID") else None)
    with connect() as c:
        c.execute("INSERT INTO events(ts,kind,message,run_id,clip_id) VALUES(?,?,?,?,?)", (now(), kind, message, run_id, clip_id))


def upsert_clip(job, pick, **f):
    run_id = int(os.environ["DESK_RUN_ID"]) if os.environ.get("DESK_RUN_ID") else None
    with connect() as c:
        r = c.execute("SELECT id FROM clips WHERE job=? AND pick=?", (job, pick)).fetchone()
        if r:
            if f:
                c.execute(f"UPDATE clips SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), r["id"]))
            return r["id"]
        cols = ["job", "pick", "run_id", "created_at", *f]
        cur = c.execute(f"INSERT INTO clips({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                        (job, pick, run_id, now(), *f.values()))
        return cur.lastrowid


def import_env():
    env = Path.home() / ".config" / "gta-desk" / "telegram.env"
    if not env.exists():
        return False
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() == "TELEGRAM_BOT_TOKEN" and v.strip():
                put("telegram_bot_token", v.strip())
            if k.strip() == "TELEGRAM_CHAT_ID" and v.strip():
                put("telegram_chat_id", v.strip())
    env.unlink()
    return True


# ---------- schedule (set on the admin Settings page) ----------

SCHED_DEFAULT = {"from": "09:00", "to": "23:00", "every": 60}


def _mins(hhmm):
    h, m = (int(x) for x in str(hhmm).split(":")[:2])
    return (h % 24) * 60 + m % 60


def schedule_cfg():
    cfg = dict(SCHED_DEFAULT)
    for k in cfg:
        v = get(f"schedule_{k}")
        if v:
            cfg[k] = int(v) if k == "every" else v
    return cfg


def schedule_slots(cfg=None):
    """Clock times (minutes after midnight) the run starts at: from, from+every, ... up to `to`.
    to <= from means an overnight window (e.g. 20:00 -> 02:00)."""
    cfg = cfg or schedule_cfg()
    a, b, step = _mins(cfg["from"]), _mins(cfg["to"]), max(15, int(cfg["every"]))
    if b <= a:
        b += 24 * 60
    return sorted({t % (24 * 60) for t in range(a, b + 1, step)})


def in_window(t=None, grace=15, cfg=None):
    """True if clock time t (minutes, default now) is inside from..to (+grace for a late start after sleep)."""
    cfg = cfg or schedule_cfg()
    if t is None:
        lt = time.localtime(); t = lt.tm_hour * 60 + lt.tm_min
    a, b = _mins(cfg["from"]), _mins(cfg["to"])
    if b <= a:
        b += 24 * 60  # overnight window
    if t < a:
        t += 24 * 60
    return a <= t <= b + grace


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run-start"); s.add_argument("--trigger", default="schedule")
    e = sub.add_parser("run-end"); e.add_argument("id", type=int); e.add_argument("status"); e.add_argument("logfile")
    e.add_argument("--summary-file")
    v = sub.add_parser("event"); v.add_argument("kind"); v.add_argument("message"); v.add_argument("--run", type=int)
    v.add_argument("--clip", type=int)
    sub.add_parser("import-env")
    sub.add_parser("in-window")
    a = ap.parse_args()
    if a.cmd == "run-start":
        with connect() as c:
            rid = c.execute("INSERT INTO runs(started_at,trigger) VALUES(?,?)", (now(), a.trigger)).lastrowid
        event("run_start", f"run {rid} started ({a.trigger})", run_id=rid)
        print(rid)
    elif a.cmd == "run-end":
        log = Path(a.logfile).read_text(encoding="utf-8", errors="replace") if Path(a.logfile).exists() else ""
        summ = Path(a.summary_file).read_text(encoding="utf-8", errors="replace").strip() if a.summary_file and Path(a.summary_file).exists() else None
        with connect() as c:
            c.execute("UPDATE runs SET finished_at=?, status=?, log=?, summary=COALESCE(?, summary) WHERE id=?",
                      (now(), a.status, log[-200_000:], summ, a.id))
        event("run_end", f"run {a.id} finished: {a.status}", run_id=a.id)
    elif a.cmd == "event":
        event(a.kind, a.message, a.run, a.clip)
    elif a.cmd == "in-window":
        sys.exit(0 if in_window() else 1)
    elif a.cmd == "import-env":
        print("imported and removed telegram.env" if import_env() else "no telegram.env found")


if __name__ == "__main__":
    main()
