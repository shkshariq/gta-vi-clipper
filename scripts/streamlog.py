"""Turn `claude -p --output-format stream-json --verbose` into a readable, live log (one line per step).

usage: claude -p ... --output-format stream-json --verbose | python -u streamlog.py >> run.log
Writes the final result text to state/summary.txt if the run did not write one.
"""
import json, os, sys, time
from pathlib import Path

NOISE = ("objc[", "[ WARN", "ctranslate2", "HF_TOKEN", "unauthenticated requests")


def short(s, n=400):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + " …"


def out(line):
    print(f"{time.strftime('%H:%M:%S')}  {line}", flush=True)


def tool_line(b):
    name, i = b.get("name"), b.get("input") or {}
    if name == "Bash":
        return f"▶ {short(i.get('command', ''), 300)}"
    if name in ("Read", "Edit", "Write"):
        return f"▶ {name} {i.get('file_path', '')}"
    return f"▶ {name} {short(json.dumps(i, ensure_ascii=False), 200)}"


def result_text(c):
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text") or "[image/data]"
    lines = [l for l in str(c).splitlines() if l.strip() and not any(n in l for n in NOISE)]
    return short(" | ".join(lines[-6:]), 500) if lines else "(no output)"


def main():
    final = None
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            if not any(n in raw for n in NOISE):
                out(short(raw))
            continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            out(f"Claude started (model {ev.get('model', '?')})")
        elif t == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    out(f"💬 {short(b['text'], 600)}")
                elif b.get("type") == "tool_use":
                    out(tool_line(b))
        elif t == "user":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out(f"   {'✗' if b.get('is_error') else '↳'} {result_text(b.get('content'))}")
        elif t == "result":
            final = ev.get("result") or ""
            secs = (ev.get("duration_ms") or 0) / 1000
            out(f"{'✅' if not ev.get('is_error') else '❌'} Claude finished in {secs / 60:.1f} min, {ev.get('num_turns', '?')} steps")
            if final:
                out(f"💬 {short(final, 1500)}")
    summ = Path(os.environ.get("CLIP_DESK_HOME", ".")) / "state" / "summary.txt"
    if final and not summ.exists():
        summ.parent.mkdir(parents=True, exist_ok=True)
        summ.write_text(final.strip()[:2000], encoding="utf-8")
    sys.exit(0 if final is not None else 1)


if __name__ == "__main__":
    main()
