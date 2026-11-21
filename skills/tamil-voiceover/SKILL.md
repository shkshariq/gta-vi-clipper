---
name: tamil-voiceover
description: Default clip pipeline for this page. Turns a found source (YouTube/Twitch video or VOD) into face-free vertical clips with a Tamil hook, plus a post pack (casual Tamil voice-over summary, YouTube Shorts titles, caption, hashtags, credit) sent to Telegram. Run it automatically right after a source is found (after trend-radar / stream-finder / moment-finder picks a video), and when the user says "Tamil", "voice-over", "no face", "remove the streamer".
---

# Tamil voice-over clips

The page posts GTA 6 gameplay **without the streamer's face**, with a **Tamil hook** on screen. The human records their own
casual Tamil voice-over in their own editor, using the **summary in the post pack**. Use this instead of `clip-cutter`.

Needs an ffmpeg with libass. On macOS: `brew install ffmpeg-full` and put `/opt/homebrew/opt/ffmpeg-full/bin` first on PATH.
Telegram settings and all logs live in `<desk>/state/desk.db` (admin UI: `python admin/app.py` -> http://127.0.0.1:8765).

## Steps (no questions)

1. Moments: `python "${CLAUDE_PLUGIN_ROOT}/scripts/moments.py" "<url>" --top 6`
   (long streams: add `--start-minute 30 --max-minutes 30`). Pick the best 2 candidates, write an English hook for each.
2. `python "${CLAUDE_PLUGIN_ROOT}/scripts/tamil.py" prep jobs/<id> --pick <n> --hook "<English hook>" --translate manual`
   Finds the webcam and crops it out, writes two files with `TODO`s, exits with code 3:
   - `clip_<n>_ta_script.txt`: the Tamil transcript. Translate every TODO line and the HOOK into natural spoken Tamil
     (game words stay English, keep the `[start-end]` times). HOOK is burned on the video: under 40 characters.
   - `clip_<n>_ta_pack.txt`: the post pack. Replace every TODO:
     - **VOICE-OVER**: casual spoken Tamil summary of the whole idea of the clip, like a Tamil gaming creator talking to
       friends. Not line by line. Keep to the word count in the file so it fits the clip. No invented facts.
     - **YOUTUBE SHORTS TITLES**: 3 options under 70 characters, Tamil or Tanglish, curiosity-driven, honest.
     - **CAPTION / DESCRIPTION**: 2-4 short Tamil lines.
     - **HASHTAGS**: 5-8, always #GTA6 and #GTA6Tamil.
     The credit and source link are filled in automatically. Do not remove them.
3. Run the same `prep` again: renders `clip_<n>_ta_preview.mp4` (face-free, 1080x1920, Tamil hook only, original audio).
   `--captions word` adds the Tamil transcript as word-by-word captions, only if the human asks.
4. **No face check**: look at 2-3 frames. If a face/webcam is visible, re-run prep with `--cam x:y:w:h` or drop the clip.
5. `python "${CLAUDE_PLUGIN_ROOT}/scripts/telegram.py" send-preview jobs/<id> --pick <n>`
   Sends only the video + the pack .txt, logs the send in the DB, deletes the video files after a successful send.
   It refuses to send while the pack still has TODO lines. Text files (transcript, pack) are kept for the logs.

## Rules

- The streamer's face never appears. If the webcam cannot be removed cleanly, skip that moment.
- Never generate, clone or imitate a voice. The human records the voice-over.
- Credit the original creator in every post (the pack has it). Prefer sources covered by a clipping campaign.
- Do not upload anywhere except the human's Telegram. The human posts.

`tamil.py final --vo <file>` still exists (mixes a voice file into the clip) but is not part of the hourly flow.
