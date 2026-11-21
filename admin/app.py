"""Clip desk admin: runs, clips (Tamil transcript + post pack), Telegram log, settings, schedule.

    CLIP_DESK_HOME=~/Workspace/personal/gta-vi/desk python admin/app.py      -> http://<host>:8765

Binds DESK_HOST (default 127.0.0.1; Docker: 0.0.0.0 = reachable on the server's IP). The first admin account can only
be created with the one-time setup code printed in the log (docker logs / ./run.sh docker status). Wrong logins are
rate-limited per IP.
"""
import hmac, os, secrets, subprocess, sys, time
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, abort, redirect, render_template_string, request, session, url_for, flash
from werkzeug.security import check_password_hash, generate_password_hash

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
os.environ.setdefault("CLIP_DESK_HOME", str(REPO.parent / "desk"))
import deskdb  # noqa: E402
import rules  # noqa: E402

DESK = Path(os.environ["CLIP_DESK_HOME"]).expanduser().resolve()
PY = sys.executable
LABEL = "com.gtadesk.hourly"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
UID = os.getuid()

app = Flask(__name__)
app.secret_key = deskdb.get("flask_secret") or (deskdb.put("flask_secret", secrets.token_hex(32)) or deskdb.get("flask_secret"))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")


# ---------- helpers ----------

def q(sql, args=(), one=False):
    with deskdb.connect() as c:
        rows = c.execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("user"):
            return redirect(url_for("login"))
        return f(*a, **k)
    return w


@app.before_request
def same_origin_posts():
    if request.method == "POST":
        src = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if src and urlparse(src).netloc != request.host:
            abort(403)


IS_MAC = sys.platform == "darwin"
# launchd (Mac) / cron (Linux) / loop (Docker: scripts/scheduler.py reads schedule_enabled from the DB)
SCHED_MODE = os.environ.get("DESK_SCHEDULER") or ("launchd" if IS_MAC else "cron")
CRON_TAG = "# gtadesk-hourly"  # Linux: the schedule is the user's crontab lines carrying this tag


def _crontab():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _crontab_write(lines):
    return subprocess.run(["crontab", "-"], input="".join(l + "\n" for l in lines), capture_output=True, text=True)


class _Ok:
    returncode, stderr = 0, ""


def schedule_on():
    if SCHED_MODE == "loop":
        return deskdb.get("schedule_enabled") == "1"
    if SCHED_MODE == "launchd":
        return subprocess.run(["launchctl", "print", f"gui/{UID}/{LABEL}"], capture_output=True).returncode == 0
    return CRON_TAG in _crontab()


def schedule_off():
    if SCHED_MODE == "loop":
        deskdb.put("schedule_enabled", "0")
    elif SCHED_MODE == "launchd":
        subprocess.run(["launchctl", "bootout", f"gui/{UID}/{LABEL}"], capture_output=True)
        PLIST.unlink(missing_ok=True)
    else:
        _crontab_write([l for l in _crontab().splitlines() if CRON_TAG not in l])


def hm(t):
    return f"{t // 60:02d}:{t % 60:02d}"


def plist_xml():
    slots = "".join(f"<dict><key>Hour</key><integer>{t // 60}</integer><key>Minute</key><integer>{t % 60}</integer></dict>"
                    for t in deskdb.schedule_slots())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>{DESK / 'hourly.sh'}</string></array>
  <key>StartCalendarInterval</key><array>{slots}</array>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{DESK / 'logs' / 'launchd.log'}</string>
  <key>StandardErrorPath</key><string>{DESK / 'logs' / 'launchd.log'}</string>
</dict></plist>
"""


def schedule_load():
    (DESK / "logs").mkdir(exist_ok=True)
    if SCHED_MODE == "loop":
        deskdb.put("schedule_enabled", "1"); return _Ok()
    if SCHED_MODE == "cron":
        keep = [l for l in _crontab().splitlines() if CRON_TAG not in l]
        job = f"/bin/zsh {DESK / 'hourly.sh'} >> {DESK / 'logs' / 'cron.log'} 2>&1 {CRON_TAG}"
        return _crontab_write(keep + [f"{t % 60} {t // 60} * * * {job}" for t in deskdb.schedule_slots()])
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if schedule_on():
        subprocess.run(["launchctl", "bootout", f"gui/{UID}/{LABEL}"], capture_output=True)
    PLIST.write_text(plist_xml())
    return subprocess.run(["launchctl", "bootstrap", f"gui/{UID}", str(PLIST)], capture_output=True, text=True)


def next_run():
    lt = datetime.now(); t = lt.hour * 60 + lt.minute
    slots = deskdb.schedule_slots()
    nxt = next((x for x in slots if x > t), None)
    return (hm(nxt) if nxt is not None else f"tomorrow {hm(slots[0])}") if slots else "—"


def sched_text():
    c = deskdb.schedule_cfg()
    return f"{c['from']}–{c['to']}, every {c['every']} min"


def duration(a, b):
    try:
        s = int((datetime.strptime(b, "%Y-%m-%d %H:%M:%S") - datetime.strptime(a, "%Y-%m-%d %H:%M:%S")).total_seconds())
    except (TypeError, ValueError):
        return "—"
    h, m = divmod(s // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m {s % 60:02d}s"


def mask(tok):
    return (tok[:6] + "…" + tok[-4:]) if tok and len(tok) > 12 else ("set" if tok else "not set")


# ---------- templates ----------

BASE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clip Desk</title><style>
:root{--bg:#f6f6f4;--card:#fff;--fg:#1d1d1f;--mut:#6b6b70;--line:#e3e3e0;--acc:#d6336c;--ok:#2b8a3e;--bad:#c92a2a;--warn:#e8590c}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1d1d20;--fg:#ececee;--mut:#9a9aa2;--line:#2c2c31;--acc:#f06595;--ok:#51cf66;--bad:#ff6b6b;--warn:#ff922b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{display:flex;gap:18px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--card);flex-wrap:wrap}
header b{color:var(--acc)}header a{color:var(--fg);text-decoration:none;opacity:.8}header a:hover{opacity:1}
main{max-width:1100px;margin:0 auto;padding:20px 16px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:500;font-size:13px}
.tag{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid currentColor}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.mut{color:var(--mut)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;max-height:520px;overflow:auto;font:13px/1.5 ui-monospace,Menlo,monospace}
button,.btn{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:7px 14px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
button.sec{background:transparent;color:var(--fg);border:1px solid var(--line)}input{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);width:100%}
textarea{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);width:100%;min-height:64px;resize:vertical}
label{display:block;margin:10px 0 4px;color:var(--mut);font-size:13px}.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.stat b{font-size:24px;display:block}
.flash{padding:10px 14px;border-radius:8px;background:var(--card);border-left:4px solid var(--acc);margin-bottom:14px}
a{color:var(--acc)}.wrap{overflow-x:auto}
</style></head><body>
{% if session.user %}<header><b>🎮 Clip Desk</b><a href="{{url_for('home')}}">Dashboard</a><a href="{{url_for('clips')}}">Clips</a>
<a href="{{url_for('events')}}">Telegram &amp; logs</a><a href="{{url_for('llm_rules')}}">LLM rules</a><a href="{{url_for('settings')}}">Settings</a>
<span style="flex:1"></span><span class="mut">{{session.user}}</span><a href="{{url_for('logout')}}">Log out</a></header>{% endif %}
<main>{% for m in get_flashed_messages() %}<div class="flash">{{m}}</div>{% endfor %}{{ body|safe }}</main></body></html>"""


def page(tpl, **ctx):
    return render_template_string(BASE, body=render_template_string(tpl, **ctx))


STATUS = """{% macro st(s) %}<span class="tag {{ {'ok':'ok','sent':'ok','running':'warn','ready':'warn','failed':'bad','send_failed':'bad','no_source':'mut'}.get(s,'mut') }}">{{s}}</span>{% endmacro %}"""


# ---------- auth ----------

FAILS = {}  # ip -> recent failed login times
MAX_FAILS, FAIL_WINDOW = 5, 15 * 60


def setup_code():
    """One-time code needed to create the first admin (so a stranger cannot claim a fresh server)."""
    if q("SELECT COUNT(*) n FROM admin", one=True)["n"]:
        return None
    code = deskdb.get("setup_code")
    if not code:
        code = secrets.token_hex(4).upper(); deskdb.put("setup_code", code)
    return code


def throttled(ip):
    now = time.time()
    FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < FAIL_WINDOW]
    return len(FAILS[ip]) >= MAX_FAILS


@app.route("/login", methods=["GET", "POST"])
def login():
    first = q("SELECT COUNT(*) n FROM admin", one=True)["n"] == 0
    ip = request.remote_addr or "?"
    if request.method == "POST" and throttled(ip):
        flash("Too many wrong attempts. Wait 15 minutes."); return redirect(url_for("login"))
    if request.method == "POST":
        u, p = request.form.get("username", "").strip(), request.form.get("password", "")
        if first:
            if not hmac.compare_digest(request.form.get("code", "").strip().upper(), setup_code() or "-"):
                FAILS.setdefault(ip, []).append(time.time())
                flash("Wrong setup code. It is in the server log: ./run.sh docker status")
            elif len(u) < 3 or len(p) < 8:
                flash("Username needs 3+ characters, password 8+.")
            else:
                with deskdb.connect() as c:
                    c.execute("INSERT INTO admin(username,password_hash) VALUES(?,?)", (u, generate_password_hash(p)))
                deskdb.put("setup_code", "")
                session["user"] = u; deskdb.event("admin", f"admin account '{u}' created")
                return redirect(url_for("home"))
        else:
            r = q("SELECT * FROM admin WHERE username=?", (u,), one=True)
            if r and check_password_hash(r["password_hash"], p):
                FAILS.pop(ip, None); session.clear(); session["user"] = u
                return redirect(url_for("home"))
            FAILS.setdefault(ip, []).append(time.time())
            deskdb.event("admin", f"failed login for '{u[:40]}' from {ip}")
            flash("Wrong username or password.")
    return page("""<div class="card" style="max-width:380px;margin:60px auto">
<h2 style="margin-top:0">{{ 'Create admin account' if first else 'Log in' }}</h2>
{% if first %}<p class="mut">First visit: enter the setup code from the server log
(<code>./run.sh docker status</code>), then choose the admin username and password.</p>{% endif %}
<form method="post">{% if first %}<label>Setup code</label><input name="code" autocomplete="off" required>{% endif %}<label>Username</label><input name="username" autofocus required>
<label>Password</label><input name="password" type="password" required><br><br><button>{{ 'Create' if first else 'Log in' }}</button></form></div>""", first=first)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- pages ----------

@app.route("/")
@login_required
def home():
    runs = q("SELECT r.*, (SELECT COUNT(*) FROM clips c WHERE c.run_id=r.id) n_clips, "
             "(SELECT COUNT(*) FROM clips c WHERE c.run_id=r.id AND c.status='sent') n_sent FROM runs r ORDER BY id DESC LIMIT 40")
    stats = q("SELECT (SELECT COUNT(*) FROM runs) runs, (SELECT COUNT(*) FROM clips WHERE status='sent') sent, "
              "(SELECT COUNT(*) FROM clips WHERE status LIKE '%fail%') failed, (SELECT MAX(started_at) FROM runs) last", one=True)
    lock = DESK / "state" / "hourly.lock"
    running = lock.exists()
    cur = q("SELECT id, started_at, trigger FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1", one=True) if running else None
    started = cur["started_at"] if cur else (datetime.fromtimestamp(lock.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") if running else None)
    return page(STATUS + """
<div class="grid">
 <div class="card stat"><span class="mut">Schedule</span><b class="{{'ok' if sched else 'mut'}}">{{'On' if sched else 'Off'}}</b>
  <span class="mut">{{stext}}{% if sched %} · next {{nxt}}{% endif %} · <a href="{{url_for('settings')}}#scheduler">change</a></span>
  <form method="post" action="{{url_for('schedule')}}"><button class="sec">{{'Turn off' if sched else 'Turn on'}}</button></form></div>
 <div class="card stat"><span class="mut">Now</span><b class="{{'warn' if running else ''}}">{{'Running' if running else 'Idle'}}
  {% if running %}<span class="elapsed" data-start="{{started}}">…</span>{% endif %}</b>
  {% if running %}<span class="mut">started {{started}}{% if cur %} · <a href="{{url_for('run',rid=cur.id)}}">run {{cur.id}}</a> ({{cur.trigger}}){% endif %}</span><br>{% endif %}
  {% if not running %}<form method="post" action="{{url_for('run_now')}}"><button>Run now</button></form>{% endif %}</div>
 <div class="card stat"><span class="mut">Clips sent</span><b>{{stats.sent}}</b><span class="bad">{{stats.failed}} failed</span></div>
 <div class="card stat"><span class="mut">Runs</span><b>{{stats.runs}}</b><span class="mut">last {{stats.last or '—'}}</span></div>
</div>
<div class="card wrap"><h3 style="margin-top:0">Runs</h3><table><tr><th>#</th><th>Started</th><th>Finished</th><th>Duration</th><th>Trigger</th><th>Status</th><th>Clips sent</th><th>Summary</th></tr>
{% for r in runs %}<tr><td><a href="{{url_for('run',rid=r.id)}}">{{r.id}}</a></td><td>{{r.started_at}}</td><td>{{r.finished_at or '—'}}</td>
<td>{% if r.finished_at %}{{dur(r.started_at, r.finished_at)}}{% else %}<span class="elapsed warn" data-start="{{r.started_at}}">…</span>{% endif %}</td><td>{{r.trigger}}</td>
<td>{{st(r.status)}}</td><td>{{r.n_sent}}/{{r.n_clips}}</td><td class="mut">{{(r.summary or '')[:140]}}</td></tr>
{% else %}<tr><td colspan="8" class="mut">No runs yet. Press “Run now”.</td></tr>{% endfor %}</table></div>
<script>
function fmt(s){s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return (h?h+'h ':'')+m+'m '+String(x).padStart(2,'0')+'s'}
function tick(){document.querySelectorAll('.elapsed').forEach(e=>{const t=new Date(e.dataset.start.replace(' ','T'));if(!isNaN(t))e.textContent=fmt((Date.now()-t)/1000)})}
tick();setInterval(tick,1000);
{% if running %}setTimeout(()=>location.reload(),30000);{% endif %}
</script>""",
                runs=runs, stats=stats, sched=schedule_on(), stext=sched_text(), nxt=next_run(), running=running, started=started, cur=cur, dur=duration)


@app.route("/run/<int:rid>")
@login_required
def run(rid):
    r = q("SELECT * FROM runs WHERE id=?", (rid,), one=True) or abort(404)
    cl = q("SELECT * FROM clips WHERE run_id=? ORDER BY id", (rid,))
    ev = q("SELECT * FROM events WHERE run_id=? ORDER BY id", (rid,))
    return page(STATUS + """<p><a href="{{url_for('home')}}">← Dashboard</a></p>
<div class="card"><h2 style="margin-top:0">Run {{r.id}} {{st(r.status)}}</h2><p class="mut">{{r.started_at}} → {{r.finished_at or 'running'}} · {{r.trigger}}</p>
{% if r.summary %}<pre>{{r.summary}}</pre>{% endif %}</div>
<div class="card wrap"><h3 style="margin-top:0">Clips</h3><table><tr><th>Clip</th><th>Hook</th><th>Status</th><th>Sent</th></tr>
{% for c in cl %}<tr><td><a href="{{url_for('clip',cid=c.id)}}">{{c.source_channel}} #{{c.pick}}</a></td><td>{{c.hook}}</td><td>{{st(c.status)}}</td><td>{{c.sent_at or '—'}}</td></tr>
{% else %}<tr><td colspan="4" class="mut">No clips in this run.</td></tr>{% endfor %}</table></div>
<div class="card wrap"><h3 style="margin-top:0">Events</h3><table>{% for e in ev %}<tr><td class="mut">{{e.ts}}</td><td>{{e.kind}}</td><td>{{e.message}}</td></tr>{% endfor %}</table></div>
<div class="card"><div class="row"><h3 style="margin:0;flex:1">Log {% if live %}<span class="tag warn">● live</span>{% endif %}</h3>
<label style="margin:0"><input type="checkbox" id="follow" checked style="width:auto"> follow</label>
<label style="margin:0"><input type="checkbox" id="noise" style="width:auto"> show noise</label></div>
<pre id="log" style="max-height:640px;margin-top:10px">loading…</pre></div>
<script>
const box=document.getElementById('log'),follow=document.getElementById('follow'),noise=document.getElementById('noise');
let off=0,raw='',live={{'true' if live else 'false'}};
const NOISE=/objc\\[|\\[ WARN|ctranslate2|HF_TOKEN|unauthenticated requests|Class AVF/;
function render(){const lines=raw.split('\\n').filter(l=>noise.checked||!NOISE.test(l));box.textContent=lines.join('\\n')||'(empty so far)';if(follow.checked)box.scrollTop=box.scrollHeight}
async function poll(){try{const r=await fetch('{{url_for('run_log',rid=r.id)}}?offset='+off);const j=await r.json();
 if(j.reset){raw='';} raw+=j.text; off=j.offset; render();
 if(j.live){setTimeout(poll,2000)}else if(live){setTimeout(()=>location.reload(),1500)}}catch(e){setTimeout(poll,5000)}}
noise.onchange=render;poll();
</script>""", r=r, cl=cl, ev=ev, live=r["status"] == "running")


@app.route("/run/<int:rid>/log")
@login_required
def run_log(rid):
    """Incremental log for the live view: returns text after ?offset= (bytes) from logs/run-<id>.log."""
    r = q("SELECT status, log FROM runs WHERE id=?", (rid,), one=True) or abort(404)
    f = DESK / "logs" / f"run-{rid}.log"
    live = r["status"] == "running"
    off = request.args.get("offset", 0, type=int)
    if f.exists():
        size = f.stat().st_size
        reset = off > size
        if reset:
            off = 0
        with open(f, "rb") as fh:
            fh.seek(off)
            data = fh.read(2_000_000)
        return {"text": data.decode("utf-8", "replace"), "offset": off + len(data), "live": live, "reset": reset}
    if off == 0 and r["log"]:
        return {"text": r["log"], "offset": len(r["log"].encode()), "live": False, "reset": False}
    return {"text": "", "offset": off, "live": live, "reset": False}


@app.route("/clips")
@login_required
def clips():
    cl = q("SELECT * FROM clips ORDER BY id DESC LIMIT 200")
    return page(STATUS + """<div class="card wrap"><h3 style="margin-top:0">Clips</h3><table>
<tr><th>Created</th><th>Source</th><th>Hook</th><th>Status</th><th>Sent to Telegram</th><th>Video deleted</th></tr>
{% for c in cl %}<tr><td class="mut">{{c.created_at}}</td><td><a href="{{url_for('clip',cid=c.id)}}">{{c.source_channel}} #{{c.pick}}</a><br><span class="mut">{{(c.source_title or '')[:50]}}</span></td>
<td>{{c.hook}}</td><td>{{st(c.status)}}</td><td>{{c.sent_at or '—'}}</td><td>{{c.video_deleted_at or '—'}}</td></tr>
{% else %}<tr><td colspan="6" class="mut">No clips yet.</td></tr>{% endfor %}</table></div>""", cl=cl)


@app.route("/clip/<int:cid>")
@login_required
def clip(cid):
    c = q("SELECT * FROM clips WHERE id=?", (cid,), one=True) or abort(404)
    ev = q("SELECT * FROM events WHERE clip_id=? ORDER BY id", (cid,))
    prev = DESK / c["job"] / "edits" / f"clip_{c['pick']}_ta_preview.mp4" if not Path(c["job"]).is_absolute() else Path(c["job"]) / "edits" / f"clip_{c['pick']}_ta_preview.mp4"
    return page(STATUS + """<p><a href="{{url_for('clips')}}">← Clips</a>{% if c.run_id %} · <a href="{{url_for('run',rid=c.run_id)}}">Run {{c.run_id}}</a>{% endif %}</p>
<div class="card"><h2 style="margin-top:0">{{c.hook}} {{st(c.status)}}</h2>
<p class="mut">{{c.source_title}} · {{c.source_channel}} · {{'%.0f'|format(c.end_sec-c.start_sec) if c.end_sec else '?'}}s ·
<a href="{{c.source_url}}" target="_blank" rel="noopener">source</a></p>
<p>Sent: {{c.sent_at or '—'}} · Video deleted: {{c.video_deleted_at or '—'}}{% if c.error %} · <span class="bad">{{c.error}}</span>{% endif %}</p>
{% if can_retry %}<form method="post" action="{{url_for('retry',cid=c.id)}}"><button>Retry Telegram send</button></form>{% endif %}</div>
<div class="card"><h3 style="margin-top:0">Post pack (sent to Telegram)</h3><pre>{{c.pack or '—'}}</pre></div>
<div class="card"><h3 style="margin-top:0">Tamil transcript</h3><pre>{{c.transcript_ta or '—'}}</pre></div>
<div class="card wrap"><h3 style="margin-top:0">Events</h3><table>{% for e in ev %}<tr><td class="mut">{{e.ts}}</td><td>{{e.kind}}</td><td>{{e.message}}</td></tr>{% endfor %}</table></div>""",
                c=c, ev=ev, can_retry=c["status"] != "sent" and prev.exists())


@app.route("/events")
@login_required
def events():
    kind = request.args.get("kind", "")
    ev = q("SELECT * FROM events WHERE (?='' OR kind LIKE ?) ORDER BY id DESC LIMIT 500", (kind, f"%{kind}%"))
    return page("""<div class="card wrap"><div class="row"><h3 style="margin:0;flex:1">Telegram &amp; run log</h3>
<a href="?">all</a><a href="?kind=telegram">telegram</a><a href="?kind=run">runs</a><a href="?kind=delete">deletes</a><a href="?kind=fail">failures</a></div>
<table><tr><th>Time</th><th>Kind</th><th>Message</th><th>Run</th></tr>
{% for e in ev %}<tr><td class="mut" style="white-space:nowrap">{{e.ts}}</td><td><span class="tag {{'bad' if 'fail' in e.kind else 'ok' if e.kind=='telegram_sent' else 'mut'}}">{{e.kind}}</span></td>
<td>{{e.message}}{% if e.clip_id %} · <a href="{{url_for('clip',cid=e.clip_id)}}">clip</a>{% endif %}</td>
<td>{% if e.run_id %}<a href="{{url_for('run',rid=e.run_id)}}">{{e.run_id}}</a>{% endif %}</td></tr>
{% else %}<tr><td colspan="4" class="mut">Nothing logged yet.</td></tr>{% endfor %}</table></div>""", ev=ev)


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        act = request.form.get("act")
        if act == "telegram":
            tok = request.form.get("token", "").strip()
            if tok:
                deskdb.put("telegram_bot_token", tok)
            cid = request.form.get("chat_id", "").strip()
            if cid:
                deskdb.put("telegram_chat_id", cid)
            deskdb.event("settings", "Telegram settings updated"); flash("Telegram settings saved.")
        elif act == "scheduler":
            f, t = request.form.get("from", ""), request.form.get("to", "")
            try:
                every = int(request.form.get("every", "60"))
                deskdb._mins(f); deskdb._mins(t)
            except ValueError:
                flash("Times must look like 09:00, interval in minutes."); return redirect(url_for("settings"))
            if f == t:
                flash("From and To must differ."); return redirect(url_for("settings"))
            deskdb.put("schedule_from", f); deskdb.put("schedule_to", t); deskdb.put("schedule_every", str(max(15, every)))
            msg = f"schedule set to {sched_text()}"
            if schedule_on():
                r = schedule_load(); msg += " (applied)" if r.returncode == 0 else f" (could not apply: {r.stderr.strip()})"
            deskdb.event("schedule", msg); flash(msg[0].upper() + msg[1:] + ("" if schedule_on() else ". Turn the schedule on to start."))
        elif act in ("detect", "test"):
            cmd = ["setup"] if act == "detect" else ["notify", "🔔 Test message from Clip Desk admin"]
            r = subprocess.run([PY, str(REPO / "scripts" / "telegram.py"), *cmd], capture_output=True, text=True,
                               env={**os.environ, "CLIP_DESK_HOME": str(DESK)})
            flash((r.stdout or r.stderr or "done").strip()[-400:])
        elif act == "password":
            p = request.form.get("password", "")
            if len(p) < 8:
                flash("Password needs 8+ characters.")
            else:
                with deskdb.connect() as c:
                    c.execute("UPDATE admin SET password_hash=? WHERE username=?", (generate_password_hash(p), session["user"]))
                deskdb.event("admin", "admin password changed"); flash("Password changed.")
        return redirect(url_for("settings"))
    cfg = deskdb.schedule_cfg(); on = schedule_on()
    return page("""<div class="card" id="scheduler" style="max-width:640px"><h3 style="margin-top:0">Scheduler
<span class="tag {{'ok' if on else 'mut'}}">{{'on' if on else 'off'}}</span></h3>
<form method="post"><input type="hidden" name="act" value="scheduler"><div class="grid">
<div><label>From</label><input type="time" name="from" value="{{cfg['from']}}" required></div>
<div><label>To</label><input type="time" name="to" value="{{cfg['to']}}" required></div>
<div><label>Every</label><select name="every" style="font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);width:100%">
{% for m in [15,30,45,60,90,120,180,240,360,480,720] %}<option value="{{m}}" {{'selected' if cfg['every']==m}}>{{ (m//60)|string + ' h' if m>=60 and m%60==0 else (m|string + ' min') }}</option>{% endfor %}</select></div></div>
<br><button>Save times</button></form>
<p class="mut">Runs start at: {{slots|join(', ')}} ({{slots|length}} a day). To earlier than From = overnight (e.g. 20:00 → 02:00).
A run that is still going when the next time comes is skipped, not doubled. “Run now” on the Dashboard ignores these times.
If the Mac was asleep, a missed start runs on wake only if it is still inside the window.</p>
<form method="post" action="{{url_for('schedule')}}"><input type="hidden" name="back" value="{{url_for('settings')}}#scheduler">
<button class="{{'sec' if on else ''}}">{{'Turn schedule off' if on else 'Turn schedule on'}}</button>{% if on %} <span class="mut">next run {{nxt}}</span>{% endif %}</form></div>
<div class="card" style="max-width:640px"><h3 style="margin-top:0">Telegram</h3>
<p>Bot token: <code>{{tok}}</code> · Chat id: <code>{{chat or 'not set'}}</code></p>
<form method="post"><input type="hidden" name="act" value="telegram"><label>New bot token (leave empty to keep)</label><input name="token" autocomplete="off">
<label>Chat id (leave empty to keep)</label><input name="chat_id"><br><br><button>Save</button></form><br>
<div class="row"><form method="post"><input type="hidden" name="act" value="detect"><button class="sec">Detect chat id</button></form>
<form method="post"><input type="hidden" name="act" value="test"><button class="sec">Send test message</button></form></div>
<p class="mut">“Detect” finds your channel (bot added as admin, then post once in the channel) or else your private chat with the bot. Or type the id yourself: channels look like -100…</p></div>
<div class="card" style="max-width:640px"><h3 style="margin-top:0">Admin password</h3><form method="post"><input type="hidden" name="act" value="password">
<label>New password</label><input name="password" type="password"><br><br><button>Change</button></form></div>""",
                tok=mask(deskdb.get("telegram_bot_token")), chat=deskdb.get("telegram_chat_id"),
                cfg=cfg, on=on, nxt=next_run(), slots=[hm(t) for t in deskdb.schedule_slots(cfg)])


FONT_DIRS = ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts", str(Path.home() / "Library/Fonts")]


def font_found(name):
    if not IS_MAC:  # fontconfig knows every installed family
        r = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True)
        return name.lower() in r.stdout.lower()
    return any(Path(d, name + ext).exists() for d in FONT_DIRS for ext in (".ttc", ".ttf", ".otf"))


@app.route("/rules", methods=["GET", "POST"])
@login_required
def llm_rules():
    if request.method == "POST":
        if request.form.get("act") == "reset":
            deskdb.put(rules.KEY, "{}"); deskdb.event("rules", "LLM rules reset to defaults"); flash("Rules reset to defaults.")
        else:
            changed = rules.save({k: request.form.get(k) for k in rules.DEFAULTS})
            if changed:
                deskdb.event("rules", "LLM rules updated: " + ", ".join(changed))
                flash("Saved. The next run uses the new rules. Changed: " + ", ".join(changed))
            else:
                flash("Nothing changed.")
            if not font_found(rules.load()["font"]):
                flash(f"Note: no font file named “{rules.load()['font']}” found. It may still work if installed under another "
                      "file name; check that the hook shows letters, not boxes, in the next clip.")
        return redirect(url_for("llm_rules"))
    hist = q("SELECT ts, message FROM events WHERE kind='rules' ORDER BY id DESC LIMIT 10")
    return page("""<div class="card"><h3 style="margin-top:0">LLM rules</h3>
<p class="mut">What the hourly run looks for and how it writes. Every run builds Claude's prompt from these rules, so a change
here applies from the next run (a run that is already going keeps its rules). Fixed safety rules are at the bottom.</p></div>
<form method="post">{% for group, fs in fields %}<div class="card"><h3 style="margin-top:0">{{group}}</h3>
<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">{% for k, label, kind, d, h in fs %}
<div style="{{ 'grid-column:1/-1' if kind in ('area','lines') else '' }}"><label for="{{k}}">{{label}}</label>
{% if kind in ('area','lines') %}<textarea id="{{k}}" name="{{k}}" rows="{{ 4 if kind=='lines' or k=='extra_rules' else 2 }}">{{r[k]}}</textarea>
{% else %}<input id="{{k}}" name="{{k}}" value="{{r[k]}}" {% if kind=='int' %}type="number" min="0"{% elif kind=='float' %}type="number" step="0.1" min="0.1"{% endif %}>{% endif %}
{% if h %}<div class="mut" style="font-size:12px;margin-top:3px">{{h}}</div>{% endif %}</div>{% endfor %}</div></div>{% endfor %}
<div class="card row"><button>Save rules</button><span class="mut">Applies from the next run.</span></div></form>
<div class="card"><form method="post" onsubmit="return confirm('Reset every rule to the default?')"><input type="hidden" name="act" value="reset">
<button class="sec">Reset to defaults</button></form>
<h3>Fixed rules</h3><pre>{{hard}}</pre>
<details><summary>Preview: the full prompt the next run will get</summary><pre>{{prompt}}</pre></details></div>
<div class="card"><h3 style="margin-top:0">Change history</h3><table>{% for e in hist %}<tr><td class="mut" style="white-space:nowrap">{{e.ts}}</td><td>{{e.message}}</td></tr>
{% else %}<tr><td class="mut">No changes yet (defaults in use).</td></tr>{% endfor %}</table></div>""",
                fields=rules.FIELDS, r=rules.load(), hard=rules.HARD_RULES, prompt=rules.render(), hist=hist)


# ---------- actions ----------

@app.route("/run-now", methods=["POST"])
@login_required
def run_now():
    if (DESK / "state" / "hourly.lock").exists():
        flash("A run is already going. Wait for it to finish.")
        return redirect(url_for("home"))
    subprocess.Popen(["/bin/zsh", str(DESK / "hourly.sh")], env={**os.environ, "DESK_TRIGGER": "manual"},
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    flash("Run started. Refresh in a few minutes.")
    return redirect(url_for("home"))


@app.route("/schedule", methods=["POST"])
@login_required
def schedule():
    if schedule_on():
        schedule_off()
        deskdb.event("schedule", "schedule turned OFF"); flash("Schedule turned off.")
    else:
        r = schedule_load()
        if r.returncode == 0:
            deskdb.event("schedule", f"schedule turned ON ({sched_text()})"); flash(f"Schedule is on ({sched_text()}). Next run {next_run()}.")
        else:
            flash(f"Could not turn on the schedule: {r.stderr.strip()}")
    back = request.form.get("back", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("home"))


@app.route("/clip/<int:cid>/retry", methods=["POST"])
@login_required
def retry(cid):
    c = q("SELECT * FROM clips WHERE id=?", (cid,), one=True) or abort(404)
    r = subprocess.run([PY, str(REPO / "scripts" / "telegram.py"), "send-preview", c["job"], "--pick", str(c["pick"])],
                       capture_output=True, text=True, cwd=DESK, env={**os.environ, "CLIP_DESK_HOME": str(DESK)})
    flash((r.stdout or r.stderr).strip()[-400:] or "done")
    return redirect(url_for("clip", cid=cid))


if __name__ == "__main__":
    host, port = os.environ.get("DESK_HOST", "127.0.0.1"), int(os.environ.get("DESK_PORT", 8765))
    code = setup_code()
    if code:
        print(f"*** First visit: create the admin account with setup code {code} ***", flush=True)
    try:
        from waitress import serve  # production server (Flask's built-in one is for development)
        print(f"Clip desk admin on http://{host}:{port}", flush=True)
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, debug=False)
