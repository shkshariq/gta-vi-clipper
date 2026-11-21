"""Telegram delivery for the Tamil clip desk (Bot API, settings + log in SQLite via deskdb).

Settings live in <desk>/state/desk.db (table settings: telegram_bot_token, telegram_chat_id),
editable in the admin UI. An old ~/.config/gta-desk/telegram.env is imported once and removed.

usage:
  python telegram.py setup                              # after you messaged your bot: finds and saves your chat id
  python telegram.py send-preview jobs/<id> --pick <n>  # video + post pack .txt; logs it; deletes the video files on success
  python telegram.py cleanup jobs/<id>                  # delete remaining video/audio files of a job (text is kept)
  python telegram.py notify "text"
"""
import argparse, sys
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, run, tool
import deskdb

MAX_UPLOAD = 49 * 1024 * 1024  # Bot API upload limit is 50 MB
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".wav", ".part", ".ytdl"}


def conf():
    deskdb.import_env()
    tok = deskdb.get("telegram_bot_token")
    if not tok:
        sys.exit("Telegram bot token not set. Add it in the admin UI (Settings) and press 'Detect chat id'.")
    return {"token": tok, "chat": deskdb.get("telegram_chat_id")}


def api(c, method, files=None, **params):
    r = requests.post(f"https://api.telegram.org/bot{c['token']}/{method}", data=params, files=files,
                      timeout=300 if files else 60)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"telegram {method}: {j.get('description')}")
    return j["result"]


def chat(c):
    if not c.get("chat"):
        sys.exit("Telegram chat id not set. Message your bot, then press 'Detect chat id' in the admin UI.")
    return c["chat"]


def small_enough(mp4):
    p = Path(mp4)
    if p.stat().st_size <= MAX_UPLOAD:
        return p
    out = p.with_name(p.stem + "_tg.mp4")
    run([tool("ffmpeg"), "-y", "-v", "error", "-i", str(p), "-c:v", "libx264", "-preset", "medium", "-crf", "28",
         "-maxrate", "7M", "-bufsize", "14M", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
    return out


def notify(c, text):
    r = api(c, "sendMessage", chat_id=chat(c), text=text[:4000], disable_web_page_preview="true")
    deskdb.event("telegram_notify", text[:300])
    return r


def delete_files(paths):
    gone = []
    for p in paths:
        p = Path(p)
        if p.exists() and p.suffix.lower() in VIDEO_EXT:
            p.unlink(); gone.append(p.name)
    return gone


# ---------- commands ----------

def cmd_setup(a):
    c = conf()
    me = api(c, "getMe")
    ups = api(c, "getUpdates", timeout=0)
    # a channel (bot added as admin, or a post in it) wins over a private chat
    seen = [(u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}).get("chat") for u in ups]
    seen = [ch for ch in seen if ch]
    chans = [ch for ch in seen if ch.get("type") in ("channel", "supergroup", "group")]
    chats = chans or [ch for ch in seen if ch.get("type") == "private"]
    if not chats and not c.get("chat"):
        sys.exit(f"Bot @{me['username']} works, but no chat found. Message t.me/{me['username']} or add it as admin to your channel and post once, then try again.")
    if chats:
        deskdb.put("telegram_chat_id", str(chats[-1]["id"])); c["chat"] = str(chats[-1]["id"])
        if chats[-1].get("title"): print(f"found: {chats[-1]['title']} ({chats[-1]['type']})")
    notify(c, "✅ Clip Desk connected. New clips will arrive here with their post pack.")
    deskdb.event("settings", f"telegram connected: @{me['username']} -> chat {c['chat']}")
    print(f"connected: @{me['username']} -> chat {c['chat']}")


def cmd_send_preview(a):
    c = conf(); d = Path(a.job); e = d / "edits"
    meta = load_json(e / f"clip_{a.pick}_ta.json")
    prev = e / f"clip_{a.pick}_ta_preview.mp4"
    pack = e / f"clip_{a.pick}_ta_pack.txt"
    script = e / f"clip_{a.pick}_ta_script.txt"
    rec = dict(source_url=meta.get("source_url"), source_title=meta.get("source_title"), source_channel=meta.get("source_channel"),
               start_sec=meta["start"], end_sec=meta["end"], hook=meta.get("hook"),
               transcript_ta=script.read_text(encoding="utf-8") if script.exists() else None,
               pack=pack.read_text(encoding="utf-8") if pack.exists() else None)
    cid = deskdb.upsert_clip(str(d), a.pick, **rec)
    problem = None
    if not prev.exists():
        problem = f"preview video missing: {prev.name}"
    elif not pack.exists():
        problem = f"post pack missing: {pack.name}"
    elif "TODO" in rec["pack"]:
        problem = "post pack still has TODO lines"
    if problem:
        deskdb.upsert_clip(str(d), a.pick, status="send_failed", error=problem)
        deskdb.event("telegram_failed", problem, clip_id=cid)
        sys.exit(problem)
    dur = meta["end"] - meta["start"]
    cap = f"🎬 {meta.get('hook', '')}\n{dur:.0f}s · {meta.get('source_channel', '')}\n📄 Voice-over summary, titles, caption & hashtags in the file below."
    try:
        up = small_enough(prev)
        with open(up, "rb") as fh:
            v = api(c, "sendVideo", files={"video": (prev.name, fh, "video/mp4")}, chat_id=chat(c), caption=cap[:1024],
                    supports_streaming="true", width=1080, height=1920)
        with open(pack, "rb") as fh:
            api(c, "sendDocument", files={"document": (pack.name, fh, "text/plain")}, chat_id=chat(c),
                caption="Post pack", reply_to_message_id=v["message_id"])
    except Exception as ex:
        deskdb.upsert_clip(str(d), a.pick, status="send_failed", error=str(ex)[:300])
        deskdb.event("telegram_failed", f"{d.name} clip {a.pick}: {ex}", clip_id=cid)
        sys.exit(f"send failed: {ex}")
    deskdb.upsert_clip(str(d), a.pick, status="sent", sent_at=deskdb.now(), telegram_message_id=v["message_id"], error=None)
    deskdb.event("telegram_sent", f"{d.name} clip {a.pick} → message {v['message_id']}: {meta.get('hook', '')}", clip_id=cid)
    gone = delete_files({prev, up, e / f"clip_{a.pick}.mp4", e / f"clip_{a.pick}_ta.mp4"})
    deskdb.upsert_clip(str(d), a.pick, video_deleted_at=deskdb.now())
    deskdb.event("delete", f"{d.name} clip {a.pick}: deleted {', '.join(gone) or 'nothing'} (text files kept)", clip_id=cid)
    print(f"sent {d.name} clip {a.pick} (message {v['message_id']}); deleted {', '.join(gone)}")


def cmd_cleanup(a):
    d = Path(a.job)
    files = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXT
             and not (p.parent.name == "edits" and p.name.endswith("_ta_preview.mp4") and _unsent(d, p))]
    gone = delete_files(files)
    if gone:
        deskdb.event("delete", f"{d.name}: deleted {len(gone)} video/audio file(s): {', '.join(gone)[:250]}")
    print(f"{d.name}: deleted {len(gone)} file(s)")


def _unsent(d, preview):
    """Keep a preview that was not sent yet, so it can be retried from the admin UI."""
    pick = preview.name.split("_")[1]
    with deskdb.connect() as c:
        r = c.execute("SELECT status FROM clips WHERE job=? AND pick=?", (str(d), int(pick))).fetchone()
    return not r or r["status"] != "sent"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    p = sub.add_parser("send-preview"); p.add_argument("job"); p.add_argument("--pick", type=int, required=True)
    k = sub.add_parser("cleanup"); k.add_argument("job")
    n = sub.add_parser("notify"); n.add_argument("text")
    a = ap.parse_args()
    if a.cmd == "notify":
        notify(conf(), a.text); return
    {"setup": cmd_setup, "send-preview": cmd_send_preview, "cleanup": cmd_cleanup}[a.cmd](a)


if __name__ == "__main__":
    main()
