"""SSRF protection (spec §25) — used by EVERY URL fetch/crawl.

Blocks: non-http(s) schemes, localhost, 127.0.0.0/8, RFC1918, link-local,
cloud metadata (169.254.169.254 incl. canonical 168.63.129.16), CGNAT,
reserved ranges, internal hostnames, and DNS resolving to private IPs.
"""
import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "kubernetes.default.svc",
}

# Hostname suffixes considered internal DNS
_INTERNAL_SUFFIXES = (".internal", ".local", ".svc", ".cluster.local", ".lan", ".corp")


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_target_url(url: str) -> str:
    """Return the normalized URL if safe; raise ValueError otherwise."""
    if not url or not isinstance(url, str):
        raise ValueError("URL is required")

    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http(s) URLs are allowed")
    if not parsed.hostname:
        raise ValueError("URL hostname is required")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in URL are not allowed")

    host = parsed.hostname.lower().rstrip(".")

    # Explicit allowlist first (authorized internal targets, e.g. demo-app)
    from app.security.allowlist import is_allowlisted

    if is_allowlisted(host):
        return url

    if host in _BLOCKED_HOSTNAMES or host.endswith(_INTERNAL_SUFFIXES):
        raise ValueError(f"Blocked internal host: {host}")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve host: {host}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_private_ip(ip):
            raise ValueError(f"Host resolves to private address: {ip}")

    return url


def is_safe_target(url: str) -> bool:
    try:
        validate_target_url(url)
        return True
    except ValueError:
        return False
