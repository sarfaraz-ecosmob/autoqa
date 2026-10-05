"""Dev auto-reload wrapper for the Celery workers.

Celery has no built-in code reload. This wrapper watches the mounted
/app source tree and restarts the worker subprocess whenever a .py file
changes — the same dev experience as `uvicorn --reload`, implemented with
the standard library only (no watchfiles/pip install needed; the CPU cost
is one stat() per file per second, negligible here).

Enable with AUTOQA_CODE_RELOAD=1 (docker-compose sets it for the worker
services). Without the flag this execs the command unchanged, so production
behavior is identical to before.

Usage (docker-compose `command`):
  python /app/scripts/dev_autoreload.py celery -A app.tasks.celery_app worker ...

Notes:
- The Celery prefork pool must be torn down cleanly between runs; we start
  the command in its own process group and signal the whole group.
- Restarts are debounced: file edits arriving within 1s trigger one restart.
- Beat (scheduler) is intentionally NOT wrapped: a beat restart mid-schedule
  could double-fire a periodic task. Beat code changes still need a manual
  `docker compose restart scheduler`.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

WATCH_ROOT = "/app"
WATCH_SUFFIX = ".py"
EXCLUDE_PARTS = {".git", "__pycache__", ".pytest_cache", "data", ".mypy_cache"}
DEBOUNCE_SECONDS = 1.0
SCAN_INTERVAL = 1.0


def _snapshot() -> dict[str, float]:
    """mtime map of all watched .py files (recursive)."""
    snap: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(WATCH_ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_PARTS]
        for name in filenames:
            if name.endswith(WATCH_SUFFIX):
                path = os.path.join(dirpath, name)
                try:
                    snap[path] = os.stat(path).st_mtime
                except OSError:
                    pass
    return snap


def _spawn(cmd: list[str]) -> subprocess.Popen:
    # Start in a new process group so we can signal Celery's prefork
    # children too, not just the master.
    return subprocess.Popen(cmd, start_new_session=True)


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def main() -> int:
    if os.environ.get("AUTOQA_CODE_RELOAD") != "1":
        # Reload disabled: hand off to the real command unchanged.
        os.execvp(sys.argv[1], sys.argv[1:])

    cmd = sys.argv[1:]
    if not cmd:
        print("dev_autoreload: no command given", file=sys.stderr)
        return 2

    print(f"[autoreload] watching {WATCH_ROOT}/**/*.py — Ctrl+C stops", flush=True)
    known = _snapshot()
    proc = _spawn(cmd)
    last_change = 0.0

    while True:
        try:
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\n[autoreload] stopping", flush=True)
            _stop(proc)
            return 0

        current = _snapshot()
        changed = (
            {p for p, m in current.items() if known.get(p) != m}
            | set(known) - set(current)
        )
        if not changed:
            # Restart the worker if it died on its own (e.g. OOM) so the
            # dev loop doesn't stall on a dead service.
            if proc.poll() is not None:
                code = proc.returncode
                print(f"[autoreload] worker exited (code {code}) — restarting", flush=True)
                proc = _spawn(cmd)
            continue

        known = current
        now = time.monotonic()
        if now - last_change < DEBOUNCE_SECONDS:
            continue  # editor save burst — wait for it to settle
        last_change = now
        files = ", ".join(sorted(os.path.basename(p) for p in changed)[:5])
        print(f"[autoreload] changed: {files} — restarting worker", flush=True)
        _stop(proc)
        proc = _spawn(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
