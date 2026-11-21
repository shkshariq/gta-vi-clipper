"""Scheduler loop for Docker (or any box without launchd/cron): starts hourly.sh at the Settings page times.

The admin UI's schedule on/off switch sets `schedule_enabled` in desk.db (DESK_SCHEDULER=loop); times come from
schedule_from/to/every. hourly.sh itself skips a start while a run is still going (lock).
"""
import os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import deskdb  # noqa: E402

DESK = Path(os.environ.get("CLIP_DESK_HOME", ".")).resolve()


def main():
    print(f"scheduler: watching {DESK} (times from the admin Settings page)", flush=True)
    last, procs = None, []
    while True:
        procs = [p for p in procs if p.poll() is None]  # reap finished runs
        try:
            if deskdb.get("schedule_enabled") == "1":
                lt = time.localtime(); t = lt.tm_hour * 60 + lt.tm_min
                key = (time.strftime("%Y-%m-%d"), t)
                if t in deskdb.schedule_slots() and key != last:
                    last = key
                    print(f"scheduler: {time.strftime('%F %T')} starting a run", flush=True)
                    log = open(DESK / "logs" / "scheduler.log", "a")
                    procs.append(subprocess.Popen(["/bin/zsh", str(DESK / "hourly.sh")], cwd=DESK, stdout=log, stderr=log,
                                                  env={**os.environ, "DESK_TRIGGER": "schedule"}))
        except Exception as e:  # keep the loop alive (e.g. db busy)
            print(f"scheduler: {e}", flush=True)
        time.sleep(20)


if __name__ == "__main__":
    main()
