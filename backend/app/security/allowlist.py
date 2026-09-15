"""Authorized internal targets (spec §25: allowlisting).

The SSRF guard blocks private networks by default. Owners may explicitly
allow specific internal hosts (e.g. the demo-app used as the default test
target). The allowlist is per-environment configuration — never committed
secrets — and defaults to the compose demo app.
"""
import os

_INTERNAL_ALLOWLIST: set[str] = {
    h.strip().lower()
    for h in os.environ.get("AUTOQA_INTERNAL_TARGETS", "demo-app,demo-app:9000").split(",")
    if h.strip()
}


def is_allowlisted(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host in _INTERNAL_ALLOWLIST:
        return True
    # Also allow bare hostname of allowlisted entries (port stripped)
    return any(host == h.split(":")[0] for h in _INTERNAL_ALLOWLIST)
