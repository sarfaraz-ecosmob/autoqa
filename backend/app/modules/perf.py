"""Performance smoke testing (spec §14) — bounded, configurable, safe.

Defaults are deliberately small (smoke-level). Production targets require
explicit authorization and configuration; the executor never exceeds the
configured request budget (max_requests hard cap).
"""
import threading
import time
from dataclasses import dataclass, field

import httpx

from app.security.ssrf import validate_target_url


@dataclass
class PerfConfig:
    path: str = "/"
    method: str = "GET"
    concurrent_users: int = 2          # spec §14 (kept small for smoke)
    duration_seconds: int = 10
    ramp_up_seconds: int = 2
    requests_per_second: float = 5.0   # rate-bounded
    max_response_time_ms: int = 3000
    max_requests: int = 200            # HARD CAP — safety valve


@dataclass
class PerfResult:
    total_requests: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    duration_seconds: float = 0.0

    def percentiles(self) -> dict:
        if not self.latencies_ms:
            return {"p50": 0, "p90": 0, "p95": 0, "p99": 0, "avg": 0}
        xs = sorted(self.latencies_ms)

        def pct(p: float) -> float:
            k = max(0, min(len(xs) - 1, int(round(p / 100 * len(xs) + 0.5)) - 1))
            return round(xs[k], 1)

        return {
            "p50": pct(50), "p90": pct(90), "p95": pct(95), "p99": pct(99),
            "avg": round(sum(xs) / len(xs), 1),
        }

    def as_dict(self) -> dict:
        p = self.percentiles()
        return {
            "total_requests": self.total_requests,
            "errors": self.errors,
            "error_rate": round(self.errors / self.total_requests, 4) if self.total_requests else 0,
            "throughput_rps": round(self.total_requests / self.duration_seconds, 2) if self.duration_seconds else 0,
            "duration_seconds": round(self.duration_seconds, 2),
            **p,
        }


def run_perf(base_url: str, config: PerfConfig) -> dict:
    """Bounded RPS load against a single endpoint; returns spec §14 metrics."""
    base_url = validate_target_url(base_url).rstrip("/")
    url = f"{base_url}{config.path}"
    result = PerfResult()
    lock = threading.Lock()
    stop_event = threading.Event()
    interval = 1.0 / max(config.requests_per_sec(), 0.1) if hasattr(config, "requests_per_sec") else 1.0 / max(config.requests_per_second, 0.1)

    def worker(stagger: float) -> None:
        time.sleep(stagger)
        with httpx.Client(timeout=10.0) as client:
            while not stop_event.is_set():
                with lock:
                    if result.total_requests >= config.max_requests:
                        return
                start = time.perf_counter()
                try:
                    resp = client.request(config.method, url)
                    ok = resp.status_code < 500
                except httpx.HTTPError:
                    ok = False
                elapsed_ms = (time.perf_counter() - start) * 1000
                with lock:
                    result.total_requests += 1
                    if not ok:
                        result.errors += 1
                    result.latencies_ms.append(elapsed_ms)
                # rate limit: each worker waits its share
                stop_event.wait(interval * config.concurrent_users)

    threads = [
        threading.Thread(target=worker, args=(i * (config.ramp_up_seconds / max(config.concurrent_users, 1)),), daemon=True)
        for i in range(config.concurrent_users)
    ]
    started = time.perf_counter()
    for t in threads:
        t.start()
    stop_event.wait(config.duration_seconds)
    stop_event.set()
    for t in threads:
        t.join(timeout=5)
    result.duration_seconds = time.perf_counter() - started

    out = result.as_dict()
    out["threshold_breached"] = (
        out["p95"] > config.max_response_time_ms
        or out["error_rate"] > 0.05
    )
    out["config"] = {
        "path": config.path,
        "concurrent_users": config.concurrent_users,
        "duration_seconds": config.duration_seconds,
        "requests_per_second": config.requests_per_second,
        "max_response_time_ms": config.max_response_time_ms,
    }
    return out
