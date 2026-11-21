"""LLM rules for the hourly run: niche, search, source selection, moments, language, post pack.

The rules live in SQLite (settings key `llm_rules`, JSON) and are edited on the admin UI page /rules.
Every hourly run renders them into the prompt headless Claude follows (desk/hourly-prompt.md is regenerated
each run, do not edit it by hand). tamil.py also reads the language, font and hashtag rules from here.

  python rules.py render [--out file]   -> prompt text
  python rules.py show                  -> current rules as JSON
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import deskdb  # noqa: E402

KEY = "llm_rules"

# (key, label, kind, default, help). kind: text (one line), area (multi-line), int, float, lines (one item per line)
FIELDS = [
    ("Niche & search", [
        ("niche", "Niche / game", "text", "GTA 6", "Used in the prompt, notices and the pack header."),
        ("search_queries", "YouTube search queries", "lines", "gta 6 gameplay\ngta 6", "One per line. All are searched every run."),
        ("search_hours", "Only videos from the last N hours", "int", 24, ""),
        ("search_limit", "Results per query", "int", 60, ""),
    ]),
    ("Source video selection", [
        ("source_include", "Pick videos that are", "area",
         "real gameplay (driving, chases, stunts, exploring, funny moments, gameplay with commentary)", ""),
        ("source_skip", "Skip videos that are", "area",
         "pure news, reviews, \"before you buy\", trailers, comparisons, story-cutscene spoilers, leaks", ""),
        ("source_rank", "Ranking", "area",
         "highest views/hour among what is left; a \"most replayed: yes\" video is a plus", ""),
        ("source_length", "Length rule", "text", "any length is fine, including multi-hour streams/VODs", ""),
        ("whole_video_max_min", "Analyse the whole video if it is up to N minutes", "int", 60, ""),
        ("window_start_min", "Longer videos: start at minute", "int", 30, "Skips the intro of long videos/streams."),
        ("very_long_hours", "Very long stream: over N hours", "int", 4, ""),
        ("very_long_start_min", "Very long stream: start at minute", "int", 90, ""),
        ("window_min", "Longer videos: window length (minutes)", "int", 15, "Section downloads run at about 2x real time."),
    ]),
    ("Clips (moments)", [
        ("clips_per_run", "Clips per run", "int", 2, ""),
        ("candidates_top", "Candidate moments to find", "int", 6, ""),
        ("moment_rules", "A good moment has", "area",
         "clear action and payoff, no spoilers, game on screen, not a menu or waiting screen", ""),
    ]),
    ("Language", [
        ("language", "Language", "text", "Tamil", "Transcript, hook, voice-over, titles and caption are written in it. Changing it? Also reword the style boxes (writing style, voice-over, titles, caption, hashtags) that name Tamil."),
        ("language_code", "Language code", "text", "ta", "ISO code (ta, hi, te, ml, kn, …). Used for Google translate mode."),
        ("font", "Font for the on-screen hook", "text", "Tamil Sangam MN",
         "Must be installed and have this language's letters (e.g. Kohinoor Devanagari, Telugu Sangam MN, Malayalam Sangam MN)."),
        ("words_per_sec", "Voice-over speed (words per second)", "float", 1.8, "Sets the voice-over word count for each clip."),
        ("hook_max_chars", "On-screen hook max characters", "int", 40, ""),
        ("language_style", "Writing style", "area",
         "natural spoken Tamil; game words stay English", ""),
    ]),
    ("Post pack (.txt sent to Telegram)", [
        ("voiceover_style", "Voice-over summary style", "area",
         "a casual, spoken Tamil summary of the whole idea of the clip, like a Tamil gaming creator talking to friends "
         "(\"macha\", \"vera level\" style is fine). It does NOT follow the transcript line by line. No invented facts.", ""),
        ("titles_count", "Number of YouTube Shorts titles", "int", 3, ""),
        ("title_max_chars", "Title max characters", "int", 70, ""),
        ("title_style", "Title style", "area", "Tamil or Tanglish, curiosity-driven, no clickbait lies", ""),
        ("caption_rules", "Caption / description", "area", "2-4 short lines in Tamil for YouTube / Instagram / TikTok", ""),
        ("hashtags_count", "Hashtag count", "text", "5-8", ""),
        ("hashtags_required", "Always include hashtags", "text", "#GTA6 #GTA6Tamil", "Space separated."),
    ]),
    ("Anything else", [
        ("extra_rules", "Extra rules for Claude", "area", "",
         "Free text, added to the end of the prompt. E.g. \"prefer funny moments over chases\", \"skip channel X\"."),
    ]),
]

FLAT = {k: (label, kind, default, help_) for _, fs in FIELDS for k, label, kind, default, help_ in fs}
DEFAULTS = {k: v[2] for k, v in FLAT.items()}

# not editable: safety and plumbing
HARD_RULES = """Fixed rules (cannot be changed from the rules page):
- Never show the streamer's face or webcam. Never synthesize or clone a voice. Always credit the creator.
- Never upload anywhere except via telegram.py. Never change PATH, never install anything.
- Run every command in the foreground with a long Bash timeout. Never run anything in the background and never
  wait for a background task: this run ends as soon as you stop, and background work is lost.
- Create or change files with the Write/Edit tools, not with shell redirection (> or >>)."""


def coerce(key, val):
    kind, default = FLAT[key][1], FLAT[key][2]
    val = val if val is not None else default
    try:
        if kind == "int":
            return int(val)
        if kind == "float":
            return float(val)
    except (TypeError, ValueError):
        return default
    s = str(val).replace("\r\n", "\n").strip()
    if kind == "lines":
        s = "\n".join(x.strip() for x in s.split("\n") if x.strip())
    return s if s or kind == "area" else default


def load():
    try:
        saved = json.loads(deskdb.get(KEY) or "{}")
    except ValueError:
        saved = {}
    return {k: coerce(k, saved.get(k)) for k in DEFAULTS}


def save(new):
    r = {k: coerce(k, new.get(k)) for k in DEFAULTS}
    old = load()
    changed = [FLAT[k][0] for k in DEFAULTS if r[k] != old[k]]
    deskdb.put(KEY, json.dumps(r, ensure_ascii=False))
    return changed


def queries(r):
    return [x for x in r["search_queries"].split("\n") if x.strip()]


def render(r=None):
    r = r or load()
    lang, n = r["language"], r["clips_per_run"]
    S = "python ../repo/scripts"
    search = "\n".join(
        f"   - `{S}/trends.py --query {json.dumps(qr)} --hours {r['search_hours']} --limit {r['search_limit']} --out trends{i + 1}.md`"
        for i, qr in enumerate(queries(r)))
    swaps = [f"wherever it says {a}, use {b}" for a, b in (("Tamil", lang), ("GTA 6", r["niche"])) if a != b]
    swap = ("\nThe skill was written for Tamil and GTA 6: " + "; ".join(swaps) + ".") if swaps else ""
    extra = f"\n\nExtra rules from the desk owner (follow them unless they break a fixed rule):\n{r['extra_rules']}" if r["extra_rules"] else ""
    return f"""You are the hourly run of the {r['niche']} {lang} clip desk. Work in the current folder (`desk/`). PATH is already set:
`python` is the repo venv and `ffmpeg` is ffmpeg-full. Scripts live in `../repo/scripts/`.
Follow `../repo/skills/tamil-voiceover/SKILL.md` with the rules below; the rules below win over the skill.{swap}

{HARD_RULES}

1. Search, one command at a time, then read every result file:
{search}
2. Pick ONE new source video:
   - its YouTube id must NOT be listed in `state/done.txt`
   - pick: {r['source_include']}
   - skip: {r['source_skip']}
   - length: {r['source_length']}
   - ranking: {r['source_rank']}
   Immediately add its id to `state/done.txt` (one id per line, keep the existing lines), even if a later step fails.
   If nothing qualifies: `{S}/telegram.py notify "No new {r['niche']} source this hour."`, write
   "no source" into `state/summary.txt`, and finish.
3. Moments:
   - up to {r['whole_video_max_min']} minutes long: `{S}/moments.py "<url>" --top {r['candidates_top']}`
   - longer: skip the intro and only take a {r['window_min']}-minute window:
     `{S}/moments.py "<url>" --top {r['candidates_top']} --start-minute {r['window_start_min']} --max-minutes {r['window_min']}`
     (for a stream over {r['very_long_hours']} hours use `--start-minute {r['very_long_start_min']}`)
   moments.py can take 10-20 minutes: foreground, long Bash timeout.
   Read `candidates.md`, pick the best {n} moment(s) ({r['moment_rules']}). Write a short English hook for each.
4. For each pick: `{S}/tamil.py prep jobs/<id> --pick <n> --hook "<hook>" --translate manual`
   (exit code 3 is expected; the files are named `_ta` whatever the language). Then fill in, for that clip:
   a. `jobs/<id>/edits/clip_<n>_ta_script.txt`: every TODO line and the HOOK line in {lang}: {r['language_style']}.
      Keep the [start-end] times. The HOOK is burned on the video: keep it under {r['hook_max_chars']} characters.
   b. `jobs/<id>/edits/clip_<n>_ta_pack.txt`: replace every TODO. Read the {lang} transcript and `candidates.md` first.
      - VOICE-OVER (in {lang}): {r['voiceover_style']} Fit the word count given in the file so it can be read within the clip length.
      - YOUTUBE SHORTS TITLES: {r['titles_count']} options, each under {r['title_max_chars']} characters, {r['title_style']}.
      - CAPTION / DESCRIPTION: {r['caption_rules']}.
      - HASHTAGS: {r['hashtags_count']}, always including {r['hashtags_required']}.
   Then run the same prep command again (renders the preview: face-free, {lang} hook only, no captions).
5. Check 3 frames of each preview (`ffmpeg -v error -y -ss <t> -i <preview> -frames:v 1 -vf scale=360:-2 state/f.jpg`, then Read it):
   no streamer face or webcam, the {lang} hook renders (no empty boxes). If a face is visible, re-run prep with
   `--cam x:y:w:h` covering it, or drop that clip if it cannot be removed.
6. For each good clip: `{S}/telegram.py send-preview jobs/<id> --pick <n>`
   (sends the video + the pack txt, logs it, and deletes the video files after a successful send).
7. Write a 2-4 line summary of this run (source, clips sent, anything that failed) into `state/summary.txt`.

If any step fails, send a short `telegram.py notify` explaining what failed, write it into `state/summary.txt`, then stop.{extra}
"""


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("render"); s.add_argument("--out")
    sub.add_parser("show")
    a = p.parse_args()
    if a.cmd == "show":
        print(json.dumps(load(), ensure_ascii=False, indent=2))
    else:
        txt = render()
        if a.out:
            Path(a.out).write_text("<!-- generated from the admin UI rules page each run; edit the rules there -->\n" + txt, encoding="utf-8")
        else:
            print(txt)


if __name__ == "__main__":
    main()
