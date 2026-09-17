"""Container healthcheck for celery beat.

The slim runtime image ships no pgrep/ps, so we verify the beat process is
alive by scanning /proc/*/cmdline directly. Matching is exact-argument based
so healthcheck shell wrappers (whose argv embeds this script's source text in
a single argument) can never false-positive.
"""
import glob
import sys


def beat_running() -> bool:
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(path, "rb") as fh:
                args = fh.read().split(b"\0")
        except OSError:
            continue  # process vanished mid-scan
        is_celery = any(a == b"celery" or a.endswith(b"/celery") for a in args)
        if is_celery and b"beat" in args:
            return True
    return False


sys.exit(0 if beat_running() else 1)
