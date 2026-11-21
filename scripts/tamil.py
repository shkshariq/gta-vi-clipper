"""Tamil voice-over clips: no streamer face, Tamil captions, your own voice.

Two steps per clip.

  prep   python tamil.py prep  jobs/<id> --pick <n> [--hook "English hook"] [--hook-ta "தமிழ்"] [--cam auto|none|x:y:w:h]
         - finds the streamer's webcam and crops it out of the frame (the face never appears)
         - translates the clip's speech to Tamil, line by line, with timings
           (--translate manual, or if Google is unavailable: writes TODO lines and exits with code 3;
            fill them in Tamil and run prep again, which keeps the edited script)
         - writes edits/clip_<n>_ta_script.txt   (Tamil transcript, timed lines)
         - writes edits/clip_<n>_ta_pack.txt     (TODO template: Tamil voice-over summary, YouTube titles, caption,
                                                  hashtags; credit + link filled in. Sent to Telegram with the video)
         - renders edits/clip_<n>_ta_preview.mp4 (Tamil captions + original audio, use it as a teleprompter)

  final  python tamil.py final jobs/<id> --pick <n> --vo my_voice.m4a [--bed 0.12] [--vo-start 0]
         - re-reads the (edited) script, so caption fixes are applied
         - mixes your voice-over on top of the original audio, which is ducked to --bed (0 = mute it)
         - writes edits/clip_<n>_ta.mp4 + .json (for check.py) + clip_<n>_ta_post.txt (post caption)

Layout: the gameplay is cropped symmetrically so the webcam box falls outside it, shown full width over a
blurred copy of the same (face-free) frame, 1080x1920, 30 fps, -14 LUFS.
"""
import argparse, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ffprobe, load_json, run, save_json, tool
from cut import W, H, build_ass, even
from translate import sentences, translator

import rules as _rules  # language, font, speed, hashtags: edited on the admin UI /rules page
R = _rules.load()
LANG = R["language_code"]
FONT = R["font"]
if sys.platform != "darwin" and FONT.endswith(" Sangam MN"):  # macOS-only font; Linux: apt install fonts-noto-core
    FONT = "Noto Sans " + FONT[: -len(" Sangam MN")]
FONT_DIRS = ["/System/Library/Fonts/Supplemental", "/Library/Fonts", str(Path.home() / "Library/Fonts")]
FG_MAX_H = 1300
HASHTAGS = R["hashtags_required"].split()


# ---------- face-free crop ----------

def face_free_crop(cam):
    """Crop (x, y, w, h) as fractions of the source frame that leaves the webcam box out.

    A webcam sits at an edge, so cutting the same margin from both sides keeps the game centred.
    If that would cost too much width, cut the cam's rows off the top or bottom instead.
    """
    if not cam:
        return (0.0, 0.0, 1.0, 1.0)
    x, y, w, h = cam
    pad = 0.01
    m = (1 - x if x + w / 2 > 0.5 else x + w) + pad
    if m <= 0.30:
        return (round(m, 4), 0.0, round(1 - 2 * m, 4), 1.0)
    if y + h / 2 > 0.5:   # cam at the bottom: keep the rows above it
        return (0.0, 0.0, 1.0, round(max(0.4, y - pad), 4))
    top = min(0.6, y + h + pad)  # cam at the top: keep the rows below it
    return (0.0, round(top, 4), 1.0, round(1 - top, 4))


def layout(src_w, src_h, crop, ass):
    """Filter graph ending in [vo] plus the caption y position under the foreground."""
    cx, cy, cw, ch = crop
    pw, ph = even(src_w * cw), even(src_h * ch)
    fg_w = W
    fg_h = even(ph * W / pw)
    if fg_h > FG_MAX_H:
        fg_h = FG_MAX_H; fg_w = even(pw * FG_MAX_H / ph)
    fg_bottom = (H + fg_h) // 2 - 80
    cap_y = min(1700, max(1460, fg_bottom + 120))
    sub = str(ass).replace("\\", "/").replace(":", "\\:")
    fonts = next((d for d in FONT_DIRS for ext in (".ttc", ".ttf", ".otf") if Path(d, FONT + ext).exists()), None)
    fd = f":fontsdir='{fonts}'" if fonts else ""
    v = (f"[0:v]crop={pw}:{ph}:{even(src_w * cx)}:{even(src_h * cy)},setsar=1,split[a][b];"
         f"[a]scale=-2:{H},crop={W}:{H},boxblur=24:2,eq=brightness=-0.08[bg];"
         f"[b]scale={fg_w}:{fg_h}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-80[v];"
         f"[v]fps=30,ass='{sub}':shaping=complex{fd}[vo]")  # complex = HarfBuzz; Debian's default mis-orders Tamil vowel signs
    return v, cap_y


# ---------- script file ----------

def fmt(t):
    return f"{t:.1f}"


def write_script(path, lines, hook_ta, hook_en, meta):
    out = [f"# {R['language']} voice-over script: clip {meta['pick']} ({meta['end'] - meta['start']:.1f}s)",
           f"# Source: {meta.get('source_title') or ''} by {meta.get('source_channel') or ''}",
           "# Machine translated: fix any line that sounds wrong. Keep the [start-end] seconds (from clip start).",
           "# Lines starting with # are ignored. HOOK is the on-screen text for the first 2.6 s.",
           "#",
           f"# HOOK (EN): {hook_en}",
           f"HOOK: {hook_ta}",
           ""]
    for a, b, en, ta in lines:
        out += [f"# EN: {en}", f"[{fmt(a)}-{fmt(b)}] {ta}", ""]
    path.write_text("\n".join(out), encoding="utf-8")


LINE = re.compile(r"^\[\s*([\d.]+)\s*-\s*([\d.]+)\s*\]\s*(.*)$")


def read_script(path):
    hook, lines = "", []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.upper().startswith("HOOK:"):
            hook = s[5:].strip(); continue
        m = LINE.match(s)
        if m and m.group(3).strip():
            lines.append((float(m.group(1)), float(m.group(2)), m.group(3).strip()))
    todo = [x for x in lines if x[2].upper().startswith("TODO")] + ([1] if hook.upper().startswith("TODO") else [])
    if todo:
        sys.exit(f"{path} still has {len(todo)} TODO line(s). Write the {R['language']} for them, then run this again.")
    return hook, lines


def voice_lines(sents, s0, e0, max_len=6.0, gap=0.8):
    """Merge transcript sentences into lines you can read aloud: tiny fragments join their neighbour,
    a line ends at a pause > gap seconds or when it would get longer than max_len seconds."""
    out = []
    for sent in sents:
        a, b = sent[0]["s"], sent[-1]["e"]
        text = " ".join(w["w"] for w in sent).strip()
        if out and (a - out[-1][1] <= gap) and (b - out[-1][0] <= max_len or out[-1][1] - out[-1][0] < 1.0):
            out[-1] = (out[-1][0], b, out[-1][2] + " " + text)
        else:
            out.append((a, b, text))
    return [(max(0.0, a - s0), min(e0 - s0, b - s0), t) for a, b, t in out]


def translate_lines(t, texts):
    """Translate many lines in as few requests as possible (the free endpoint allows about 5 per second).

    Lines are sent together, one per row, in chunks under ~4500 chars. A chunk whose row count does not
    come back intact falls back to one request per line, paced. Retries with backoff on rate limits.
    Raises RuntimeError instead of silently keeping English.
    """
    import time

    def call(text):
        for i in range(5):
            try:
                return (t.translate(text) or "").strip()
            except Exception as e:
                if i == 4:
                    raise RuntimeError(e)
                time.sleep(2 * (i + 1))

    out, chunk, size = [], [], 0
    chunks = []
    for x in texts:
        if chunk and size + len(x) > 4500:
            chunks.append(chunk); chunk, size = [], 0
        chunk.append(x); size += len(x) + 1
    if chunk:
        chunks.append(chunk)
    for ch in chunks:
        rows = [r.strip() for r in call("\n".join(ch)).split("\n")]
        rows = [r for r in rows if r]
        if len(rows) != len(ch):
            rows = []
            for x in ch:
                rows.append(call(x)); time.sleep(0.3)
        out += rows
        time.sleep(0.5)
    return out


def caption_words(lines, s0):
    """Spread each line's Tamil words evenly over its time span (absolute source seconds for build_ass)."""
    res = []
    for a, b, text in lines:
        ws = text.split()
        step = max(0.05, (b - a) / len(ws))
        res += [{"w": x, "s": s0 + a + i * step, "e": s0 + a + (i + 1) * step} for i, x in enumerate(ws)]
    return res


def tamil_ass(words, s0, e0, hook, cap_y):
    """cut.build_ass with a Tamil font (Arial Black has no Tamil glyphs) and sizes for longer Tamil words."""
    s = build_ass(words, s0, e0, hook, cap_y)
    s = s.replace("Style: Cap,Arial Black,92,", f"Style: Cap,{FONT},70,")
    s = s.replace("Style: Hook,Arial Black,68,", f"Style: Hook,{FONT},58,")
    return s


# ---------- post pack (sent to Telegram with the video) ----------

PACK_HOOK = re.compile(r"^🖼 ON-SCREEN HOOK:.*$", re.M)


def write_pack(path, meta):
    dur = meta["end"] - meta["start"]
    words = max(25, int(dur * R["words_per_sec"]))  # spoken words per second, from the rules
    ch = meta.get("source_channel") or "original creator"
    url = meta.get("source_url") or ""
    txt = f"""🎬 {R['niche']} · {dur:.0f}s clip
Source: {meta.get('source_title') or ''} ({ch})

🎙 VOICE-OVER (casual {R['language']} summary, about {words} words, read in {dur:.0f}s)
TODO

📌 YOUTUBE SHORTS TITLES (pick one)
1. TODO
2. TODO
3. TODO

🖼 ON-SCREEN HOOK: {meta.get('hook') or ''}

✍️ CAPTION / DESCRIPTION
TODO

#️⃣ HASHTAGS
TODO

🎮 CREDIT
Gameplay: {ch}
{url}
"""
    path.write_text(txt, encoding="utf-8")


def sync_pack_hook(path, hook):
    if path.exists():
        s = path.read_text(encoding="utf-8")
        path.write_text(PACK_HOOK.sub(lambda m: f"🖼 ON-SCREEN HOOK: {hook}", s), encoding="utf-8")


# ---------- render ----------

def render(src, s0, e0, vfilter, mp4, vo=None, bed=0.12, vo_start=0.0):
    cmd = [tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{s0:.2f}", "-to", f"{e0:.2f}", "-i", src]
    if vo:
        d = int(vo_start * 1000)
        cmd += ["-i", str(vo)]
        a = (f"[0:a]volume={bed}[bed];[1:a]aresample=48000,adelay={d}|{d},apad[vo_a];"
             f"[bed][vo_a]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[ao]")
        cmd += ["-filter_complex", vfilter + ";" + a, "-map", "[vo]", "-map", "[ao]"]
    else:
        cmd += ["-filter_complex", vfilter, "-map", "[vo]", "-map", "0:a?", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-ar", "48000", "-movflags", "+faststart", str(mp4)]
    run(cmd)


def src_size(src):
    vs = next(x for x in ffprobe(src)["streams"] if x.get("codec_type") == "video")
    return int(vs["width"]), int(vs["height"])


# ---------- commands ----------

def prep(a):
    d = Path(a.job); out = d / "edits"; out.mkdir(exist_ok=True)
    c = load_json(d / "candidates.json")
    pick = next((x for x in c["candidates"] if x["n"] == a.pick), None)
    if not pick:
        sys.exit(f"candidate {a.pick} not found in {d / 'candidates.json'}")
    s0 = a.start if a.start is not None else pick["start"]
    e0 = a.end if a.end is not None else pick["end"]
    src = c["source"]

    cam, cam_note = None, "no webcam"
    if a.cam == "auto":
        from facecam import find_cam
        r = find_cam(src, s0, e0)
        if r.get("mode") in ("cam", "face"):
            cam, cam_note = list(r["box"]), f"{r.get('mode')}: {r.get('summary', '')}"
    elif a.cam != "none":
        cam, cam_note = [float(v) for v in a.cam.split(":")], "manual"
    crop = face_free_crop(cam)
    print(f"webcam: {cam_note} -> crop {crop}")

    info = c.get("info", {})
    meta = {"pick": a.pick, "start": s0, "end": e0, "source": src, "cam": cam, "crop": list(crop),
            "source_url": info.get("webpage_url"), "source_title": info.get("title"),
            "source_channel": info.get("channel") or info.get("uploader")}
    script = out / f"clip_{a.pick}_ta_script.txt"
    if script.exists() and not a.retranslate:
        print(f"keeping your existing script (use --retranslate to overwrite): {script}")
    else:
        words = [w for w in load_json(d / "transcript.json") if s0 <= w["s"] < e0 and w["w"]]
        vl = voice_lines([s for s in sentences(words) if " ".join(w["w"] for w in s).strip()], s0, e0)
        ens = [x[2] for x in vl]
        hook_en = a.hook or ""
        need_hook = bool(hook_en and not a.hook_ta)
        tas = None
        if a.translate == "google":
            try:
                tas = translate_lines(translator(LANG), ens + ([hook_en] if need_hook else []))
            except RuntimeError as e:
                print(f"google translate unavailable ({str(e)[:90]}); writing TODO lines instead", file=sys.stderr)
        if tas is None:
            tas = [f"TODO {x}" for x in ens] + ([f"TODO {hook_en}"] if need_hook else [])
        hook_ta = a.hook_ta or (tas.pop() if need_hook else "")
        lines = [(st, en_t, en, ta) for (st, en_t, en), ta in zip(vl, tas)]
        write_script(script, lines, hook_ta, hook_en, meta)
        write_pack(out / f"clip_{a.pick}_ta_pack.txt", meta)
        if any(x.startswith("TODO") for x in tas + [hook_ta]):
            print(script)
            sys.exit(3)  # Claude (or you) writes the Tamil into the TODO lines, then runs prep again
    hook, lines = read_script(script)
    meta["hook"] = hook
    meta["captions"] = a.captions
    pack = out / f"clip_{a.pick}_ta_pack.txt"
    if not pack.exists():
        write_pack(pack, meta)
    sync_pack_hook(pack, hook)
    if a.captions == "none":
        lines = []  # only the Tamil hook is burned in

    w, h = src_size(src)
    ass = out / f"clip_{a.pick}_ta.ass"
    vfilter, cap_y = layout(w, h, crop, ass)
    ass.write_text(tamil_ass(caption_words(lines, s0), s0, e0, hook, cap_y), encoding="utf-8")
    mp4 = out / f"clip_{a.pick}_ta_preview.mp4"
    render(src, s0, e0, vfilter, mp4)
    save_json(out / f"clip_{a.pick}_ta.json", meta)
    print(script)
    print(mp4)


def final(a):
    d = Path(a.job); out = d / "edits"
    side = out / f"clip_{a.pick}_ta.json"
    if not side.exists():
        sys.exit(f"{side} not found. Run: tamil.py prep {a.job} --pick {a.pick}")
    vo = Path(a.vo)
    if not vo.exists():
        sys.exit(f"voice-over file not found: {vo}")
    meta = load_json(side)
    s0, e0, src = meta["start"], meta["end"], meta["source"]
    hook, lines = read_script(out / f"clip_{a.pick}_ta_script.txt")
    if meta.get("captions", "word") == "none":
        lines = []
    vo_len = float(ffprobe(vo)["format"]["duration"])
    if a.vo_start + vo_len > (e0 - s0) + 0.5:
        print(f"warning: voice-over ({vo_len:.1f}s) runs past the clip ({e0 - s0:.1f}s); the end will be cut", file=sys.stderr)

    w, h = src_size(src)
    ass = out / f"clip_{a.pick}_ta.ass"
    vfilter, cap_y = layout(w, h, tuple(meta["crop"]), ass)
    ass.write_text(tamil_ass(caption_words(lines, s0), s0, e0, hook, cap_y), encoding="utf-8")
    mp4 = out / f"clip_{a.pick}_ta.mp4"
    render(src, s0, e0, vfilter, mp4, vo=vo, bed=a.bed, vo_start=a.vo_start)
    meta.update({"file": str(mp4), "hook": hook, "layout": "noface-ta", "voiceover": str(vo), "bed": a.bed})
    save_json(side, meta)

    credit = f"🎮 Gameplay: {meta.get('source_channel') or 'original creator'}"
    post = "\n".join(x for x in [hook, credit, meta.get("source_url") or "", " ".join(HASHTAGS)] if x)
    (out / f"clip_{a.pick}_ta_post.txt").write_text(post + "\n", encoding="utf-8")
    print(mp4)
    print(out / f"clip_{a.pick}_ta_post.txt")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("job"); p.add_argument("--pick", type=int, default=1)
    p.add_argument("--hook", default="", help="English hook, translated to Tamil")
    p.add_argument("--hook-ta", default="", help="Tamil hook as is (wins over --hook)")
    p.add_argument("--cam", default="auto", help="auto (find the webcam), none, or x:y:w:h fractions")
    p.add_argument("--start", type=float); p.add_argument("--end", type=float)
    p.add_argument("--retranslate", action="store_true", help="overwrite an existing (edited) script")
    p.add_argument("--captions", choices=["none", "word"], default="none",
                   help="none = only the Tamil hook on screen (default); word = word-by-word Tamil transcript captions")
    p.add_argument("--translate", choices=["google", "manual"], default="google",
                   help="google = machine translation; manual = write TODO lines for Claude/you to translate")
    f = sub.add_parser("final")
    f.add_argument("job"); f.add_argument("--pick", type=int, default=1)
    f.add_argument("--vo", required=True, help="your recorded voice-over (wav, m4a, mp3...)")
    f.add_argument("--bed", type=float, default=0.12, help="original audio volume under the voice (0 = mute)")
    f.add_argument("--vo-start", type=float, default=0.0, help="seconds into the clip where your voice starts")
    a = ap.parse_args()
    prep(a) if a.cmd == "prep" else final(a)


if __name__ == "__main__":
    main()
