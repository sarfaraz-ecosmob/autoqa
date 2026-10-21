"""Authenticated-target support (spec §9/§2): how AutoQA tests apps behind a login.

Three user-provided pieces (all optional):
  credentials   → dict like {"username": "qa@corp.com", "password": "..."} stored
                  Fernet-encrypted on the Project, masked in every API response.
                  Two-factor: add "otp_secret" (base32 TOTP secret, authentica-
                  tor-app style) or "otp_code" (static/backdoor code) and the
                  browser login fills the 2FA prompt automatically (RFC 6238
                  TOTP generated here on the stdlib — see generate_otp).
  auth flow     → how to sign in before tests: browser form login (selectors
                  resolved with the same self-healing as test steps) or a direct
                  API token request (token extracted from JSON and replayed as
                  Bearer / header).
  api auth      → headers auto-attached to every API test request (Bearer token
                  by default).

Credential values may use {{placeholders}} resolved from project/env/dataset
variables, so a static dataset can carry per-environment secrets. Placeholder
resolution happens inside the execution path only — auth details are never
logged, never serialized into evidence (mask_secrets() scrubs them), and
generation never embeds plaintext secrets (only {{placeholders}}).
"""
from __future__ import annotations

import base64
import hmac
import struct
import time
from typing import Any

from app.execution.api_exec import substitute_vars

_DEFAULT = {
    "login_url": "",
    "username_selector": "input[name='username']",
    "password_selector": "input[type='password']",
    "submit_selector": "button[type='submit']",
    "username_field": "username",
    "password_field": "password",
    "success_assert": "url_not_contains:login",  # or: text_present:, selector_present:, api_status:
    "api_token_request": None,  # {"path": "/api/auth/login", "json": {...}, "token_path": "token"}
    "api_auth_header": "Authorization",  # "Bearer {token}" by default; "" disables header attach
    "api_auth_prefix": "Bearer ",
    # Two-factor authentication (OTP / 2FA). The CODE itself lives in the
    # encrypted credentials values (otp_secret = base32 TOTP secret, or
    # otp_code = static/backdoor code); these keys describe the 2FA UI.
    "otp_selector": "",       # OTP input; empty = auto-detect one-time-code fields
    "otp_submit_selector": "",  # OTP-step button; empty = reuse submit_selector
}

# Auto-detect candidates for the one-time-code input, tried in order when no
# otp_selector is configured. Only ever probed when an OTP secret IS set, so
# projects without 2FA never see this path.
_OTP_INPUT_CANDIDATES = (
    "input[autocomplete='one-time-code']",
    "input[otp]",
    "input[name*='otp' i]",
    "input[id*='otp' i]",
    "input[placeholder*='otp' i]",
    "input[aria-label*='otp' i]",
    "input[data-testid*='otp' i]",
    "input[data-test*='otp' i]",
)

_OTP_WAIT_MS = 6000  # how long to wait for the 2FA prompt after submitting the form


def normalize_auth(auth: dict | None) -> dict:
    """Merge user-provided auth flow config over defaults; add flags."""
    merged = dict(_DEFAULT)
    merged.update({k: v for k, v in (auth or {}).items() if v is not None})
    merged["has_credentials"] = True
    return merged


# ---------------------------------------------------------------------------
# Two-factor authentication (OTP / 2FA)
# ---------------------------------------------------------------------------

def _pad_base32(secret: str) -> str:
    """Accept secrets pasted with or without '=' padding."""
    return secret + "=" * ((8 - len(secret) % 8) % 8)


def totp(secret: str, digits: int = 6, period: int = 30, at: float | None = None) -> str:
    """RFC 6238 TOTP (SHA-1, 6 digits) implemented on the stdlib.

    Deliberately dependency-free (no pyotp): this code runs inside the
    already-built backend/browser-worker images and a new pip requirement
    would force an image rebuild for zero benefit. `at` (epoch seconds) is
    injectable for tests — RFC 6238 Appendix B vectors use t=59.
    """
    key = base64.b32decode(_pad_base32(secret.upper()))
    counter = int((time.time() if at is None else at) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def generate_otp(credentials: dict, variables: dict) -> str:
    """The one-time code for the 2FA step: a static code (otp_code, e.g. a
    test-env backdoor code) wins when set; otherwise a fresh TOTP is derived
    from the base32 otp_secret at this moment. Returns "" when neither is
    configured (2FA disabled) or the secret is not valid base32 — callers
    then skip the OTP step instead of failing the whole login."""
    static = substitute_vars(str(credentials.get("otp_code") or ""), variables).strip()
    if static:
        return static
    secret = substitute_vars(str(credentials.get("otp_secret") or ""), variables).strip()
    secret = "".join(secret.split())  # allow spaces in pasted secrets
    if not secret:
        return ""
    try:
        return totp(secret)
    except Exception:  # noqa: BLE001 — invalid base32 etc.
        return ""


def _assert_pass(assertion: str, page: Any, token_status: int | None) -> bool:
    """Evaluate `kind:value` success assertions after a login.

    Unknown kinds and empty values fail-safe (False) so a misconfigured
    success check surfaces in the auth log instead of passing vacuously.
    """
    kind, _, value = assertion.partition(":")
    kind = kind.strip().lower()
    value = value.strip()
    if not kind or (not value and kind != "api_status"):
        return False
    try:
        if kind == "url_not_contains":
            return value.lower() not in page.url.lower()
        if kind == "url_contains":
            return value.lower() in page.url.lower()
        if kind == "text_present":
            return page.get_by_text(value).count() > 0
        if kind == "selector_present":
            return page.locator(value).count() > 0
        if kind == "api_status":
            return token_status is not None and 200 <= token_status < 300
    except Exception:
        return False
    return False


def _otp_selector_candidates(auth: dict, variables: dict) -> list[str]:
    """Ordered list of selectors to probe for the OTP input."""
    configured = substitute_vars(str(auth.get("otp_selector") or ""), variables).strip()
    return [configured] if configured else list(_OTP_INPUT_CANDIDATES)


def _find_otp_input_sync(page: Any, auth: dict, variables: dict) -> str | None:
    """Return the selector of a visible OTP input, waiting up to
    _OTP_WAIT_MS for the 2FA prompt to appear after the password submit.
    Returns None when no prompt shows up (site skipped 2FA this session)."""
    candidates = _otp_selector_candidates(auth, variables)
    attempts = max(1, _OTP_WAIT_MS // 250)
    for i in range(attempts):
        for cand in candidates:
            try:
                if page.locator(cand).count() > 0:
                    return cand
            except Exception:  # noqa: BLE001 — bad selector, try the next
                continue
        if i == attempts - 1:
            break
        try:
            page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001
            break
    return None


def _otp_step_sync(page: Any, auth: dict, credentials: dict, variables: dict, log: list) -> bool | None:
    """Fill + submit the second-factor step when the target asks for it.

    Returns True when the OTP step was handled, False when it was prompted
    but failed (bad secret / unfillable input — the login cannot succeed),
    and None when no OTP step appeared or 2FA is not configured. Projects
    without otp_code/otp_secret never enter this path, so plain logins
    behave exactly as before. The code value is never logged."""
    code = generate_otp(credentials, variables)
    if not code:
        return None
    selector = _find_otp_input_sync(page, auth, variables)
    if not selector:
        log.append({"action": "auth_otp", "result": "skipped", "note": "no OTP prompt detected"})
        return None
    try:
        page.fill(selector, code, timeout=5000)
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_otp", "result": "fail", "error": f"otp fill failed: {exc}"[:200]})
        return False
    submit = substitute_vars(str(auth.get("otp_submit_selector") or ""), variables).strip() or substitute_vars(
        str(auth.get("submit_selector") or ""), variables
    )
    try:
        clicked = False
        if submit:
            try:
                if page.locator(submit).count() > 0:
                    page.click(submit, timeout=5000)
                    clicked = True
            except Exception:  # noqa: BLE001
                pass
        if not clicked:
            page.press(selector, "Enter")  # Enter on the OTP field is the common fallback
        try:
            page.wait_for_load_state("load", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(300)  # let the app settle after the 2FA submit
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_otp", "result": "fail", "error": f"otp submit failed: {exc}"[:200]})
        return False
    log.append({"action": "auth_otp", "result": "pass"})
    return True


def browser_login(page: Any, auth: dict, credentials: dict, variables: dict, log: list) -> bool:
    """Sign in on an existing Playwright page using the project's auth flow.

    Selectors and values go through {{placeholder}} substitution and the same
    self-healing fallbacks as regular test steps. Returns True when the
    success assertion passes (or when no assertion is configured).
    """
    from app.execution.healing import heal_selector

    login_url = substitute_vars(auth.get("login_url") or "", variables)
    if not login_url:
        return False
    try:
        page.goto(login_url, timeout=15000, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_login", "result": "fail", "error": f"goto failed: {exc}"[:200]})
        return False

    def _resolve(selector: str) -> str:
        target = substitute_vars(selector, variables)
        try:
            if page.locator(target).count() > 0:
                return target
        except Exception:
            pass
        try:
            healed, method = heal_selector(page, target)
            if healed:
                log.append({"action": "auth_heal", "healed_from": target, "healed_to": healed, "method": method})
                return healed
        except Exception:
            pass
        return target

    username = substitute_vars(str(credentials.get("username", "")), variables)
    password = substitute_vars(str(credentials.get("password", "")), variables)

    try:
        page.fill(_resolve(auth.get("username_selector") or "input[name='username']"), username, timeout=5000)
        page.fill(_resolve(auth.get("password_selector") or "input[type='password']"), password, timeout=5000)
        page.click(_resolve(auth.get("submit_selector") or "button[type='submit']"), timeout=5000)
        # A form submit usually triggers a navigation; wait for the next load
        # state (no-op for SPA logins) so the success assertion sees the final
        # page instead of the still-navigating one.
        try:
            page.wait_for_load_state("load", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(300)  # let the app settle after submit
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_login", "result": "fail", "error": f"form submit failed: {exc}"[:200]})
        return False

    # ---- Optional second factor (only when an OTP secret/code is set) ----
    if _otp_step_sync(page, auth, credentials, variables, log) is False:
        return False

    assertion = substitute_vars(str(auth.get("success_assert") or ""), variables).strip()
    if not assertion or assertion.lower() == "none":
        log.append({"action": "auth_login", "result": "pass", "note": "no success assertion configured"})
        return True
    ok = _assert_pass(assertion, page, None)
    log.append({"action": "auth_login", "result": "pass" if ok else "fail", "assert": assertion[:100]})
    return ok


def fetch_api_token(auth: dict, credentials: dict, variables: dict, base_url: str) -> str:
    """Request a token from the target's auth endpoint and extract it.

    Uses the configured api_token_request ({path, method, json|form|body,
    headers, token_path}); {{placeholders}} in path/body/headers/credentials
    resolve from test-data variables with {{username}}/{{password}} coming
    from the credential values. Returns "" when no request is configured or
    the token path misses — callers proceed unauthenticated (tests then
    surface the real behavior instead of masking it).
    """
    import httpx

    from app.security.ssrf import validate_target_url

    spec = auth.get("api_token_request") or {}
    if not isinstance(spec, dict) or not spec.get("path"):
        return ""
    try:
        base_url = validate_target_url(base_url)
    except ValueError:
        return ""

    path = substitute_vars(str(spec.get("path", "")), variables)
    creds = {
        "username": substitute_vars(str(credentials.get("username", "")), variables),
        "password": substitute_vars(str(credentials.get("password", "")), variables),
    }
    # {{username}}/{{password}} in the request spec resolve from the
    # credential values (over variables, so datasets can still override).
    # {{otp}} resolves to the current one-time code when 2FA is configured
    # (some APIs accept TOTP directly in the login payload).
    sub = {**variables, **creds, "otp": generate_otp(credentials, variables)}
    json_payload = substitute_vars(spec.get("json"), sub)
    form_payload = substitute_vars(spec.get("form") or spec.get("data"), sub)
    body_payload = substitute_vars(spec.get("body"), sub)

    headers = substitute_vars(spec.get("headers"), sub) or {}
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=20.0) as client:
            resp = client.request(
                str(spec.get("method", "POST")).upper(),
                path,
                json=json_payload if isinstance(json_payload, (dict, list)) else None,
                data=form_payload if isinstance(form_payload, dict) else None,
                content=body_payload if isinstance(body_payload, str) else None,
                headers=headers or None,
            )
    except httpx.HTTPError:
        return ""

    from app.execution.api_exec import extract_path

    token = extract_path(resp.json() if _is_json(resp) else {}, str(spec.get("token_path", "token")))
    if token is None:
        return ""
    return str(token)


def _is_json(resp: Any) -> bool:
    try:
        resp.json()
        return True
    except Exception:
        return False


def resolve_api_auth_headers(
    auth: dict, credentials: dict, variables: dict, base_url: str, log: list
) -> dict:
    """Headers attached to every API test request (already {{resolved}}).

    Precedence: a static header value in api_auth_header_value (may hold a
    pre-provisioned token) → token fetched from api_token_request → none.
    """
    header = str(auth.get("api_auth_header") or "")
    if not header:  # explicitly disabled
        return {}

    static_value = substitute_vars(str(auth.get("api_auth_header_value") or ""), variables)
    if static_value:
        value = static_value
        prefix = str(auth.get("api_auth_prefix") or "")
        if prefix and not value.lower().startswith(prefix.strip().lower()):
            value = f"{prefix}{value}"  # accept raw token or full "Bearer …" paste
        return {header: value}

    prefix = str(auth.get("api_auth_prefix") or "")
    token = fetch_api_token(auth, credentials, variables, base_url)
    if token:
        log.append({"action": "auth_token_fetch", "result": "pass"})
        return {header: f"{prefix}{token}"}
    if auth.get("api_token_request"):
        log.append({"action": "auth_token_fetch", "result": "fail", "note": "token request returned no token"})
    return {}


def verify_login_flow(page: Any, auth: dict, credentials: dict, variables: dict, log: list) -> dict:
    """Login + assert the protected area is reachable (used for §6 'Verify
    valid login succeeds' cases). Never returns credential material."""
    ok = browser_login(page, auth, credentials, variables, log)
    return {"passed": ok, "evidence": "auth_flow"}


# ---------------------------------------------------------------------------
# Async twin (discovery crawler runs on asyncio/Playwright-async; the sync
# version above drives sync Playwright in the test executor).
# ---------------------------------------------------------------------------


def _login_assertion(auth: dict, variables: dict) -> str:
    """Shared success-assertion string for both login implementations."""
    return substitute_vars(str(auth.get("success_assert") or ""), variables).strip()


async def _find_otp_input_async(page: Any, auth: dict, variables: dict) -> str | None:
    """Async twin of _find_otp_input_sync (page methods awaited)."""
    candidates = _otp_selector_candidates(auth, variables)
    attempts = max(1, _OTP_WAIT_MS // 250)
    for i in range(attempts):
        for cand in candidates:
            try:
                if await page.locator(cand).count() > 0:
                    return cand
            except Exception:  # noqa: BLE001 — bad selector, try the next
                continue
        if i == attempts - 1:
            break
        try:
            await page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001
            break
    return None


async def _otp_step_async(page: Any, auth: dict, credentials: dict, variables: dict, log: list) -> bool | None:
    """Async twin of _otp_step_sync for the discovery crawler / dry runs."""
    code = generate_otp(credentials, variables)
    if not code:
        return None
    selector = await _find_otp_input_async(page, auth, variables)
    if not selector:
        log.append({"action": "auth_otp", "result": "skipped", "note": "no OTP prompt detected"})
        return None
    try:
        await page.fill(selector, code, timeout=5000)
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_otp", "result": "fail", "error": f"otp fill failed: {exc}"[:200]})
        return False
    submit = substitute_vars(str(auth.get("otp_submit_selector") or ""), variables).strip() or substitute_vars(
        str(auth.get("submit_selector") or ""), variables
    )
    try:
        clicked = False
        if submit:
            try:
                if await page.locator(submit).count() > 0:
                    await page.click(submit, timeout=5000)
                    clicked = True
            except Exception:  # noqa: BLE001
                pass
        if not clicked:
            await page.press(selector, "Enter")  # Enter fallback
        try:
            await page.wait_for_load_state("load", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(300)  # let the app settle after the 2FA submit
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_otp", "result": "fail", "error": f"otp submit failed: {exc}"[:200]})
        return False
    log.append({"action": "auth_otp", "result": "pass"})
    return True


async def browser_login_async(page: Any, auth: dict, credentials: dict, variables: dict, log: list) -> bool:
    """Async twin of browser_login for the discovery crawler (async Playwright).

    Same flow: goto → fill username/password (self-healed selectors) → submit
    → evaluate the success assertion. Returns True when the assertion passes
    (or when none is configured)."""
    login_url = substitute_vars(auth.get("login_url") or "", variables)
    if not login_url:
        return False
    try:
        await page.goto(login_url, timeout=15000, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_login", "result": "fail", "error": f"goto failed: {exc}"[:200]})
        return False

    async def _resolve(selector: str) -> str:
        target = substitute_vars(selector, variables)
        try:
            if await page.locator(target).count() > 0:
                return target
        except Exception:
            pass
        try:
            from app.execution.healing import heal_selector_async

            healed, method = await heal_selector_async(page, target)
            if healed:
                log.append({"action": "auth_heal", "healed_from": target, "healed_to": healed, "method": method})
                return healed
        except Exception:
            pass
        return target

    username = substitute_vars(str(credentials.get("username", "")), variables)
    password = substitute_vars(str(credentials.get("password", "")), variables)

    try:
        await page.fill(await _resolve(auth.get("username_selector") or "input[name='username']"), username, timeout=5000)
        await page.fill(await _resolve(auth.get("password_selector") or "input[type='password']"), password, timeout=5000)
        await page.click(await _resolve(auth.get("submit_selector") or "button[type='submit']"), timeout=5000)
        # A form submit usually triggers a navigation; wait for the next load
        # state (no-op for SPA logins) so the success assertion sees the final
        # page instead of the still-navigating one.
        try:
            await page.wait_for_load_state("load", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(300)  # let the app settle after submit
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_login", "result": "fail", "error": f"form submit failed: {exc}"[:200]})
        return False

    # ---- Optional second factor (only when an OTP secret/code is set) ----
    if await _otp_step_async(page, auth, credentials, variables, log) is False:
        return False

    assertion = _login_assertion(auth, variables)
    if not assertion or assertion.lower() == "none":
        log.append({"action": "auth_login", "result": "pass", "note": "no success assertion configured"})
        return True
    ok = await _assert_pass_async(assertion, page)
    log.append({"action": "auth_login", "result": "pass" if ok else "fail", "assert": assertion[:100]})
    return ok


async def _assert_pass_async(assertion: str, page: Any) -> bool:
    """Async twin of _assert_pass (page methods are awaited)."""
    kind, _, value = assertion.partition(":")
    kind = kind.strip().lower()
    value = value.strip()
    if not kind or (not value and kind != "api_status"):
        return False
    try:
        if kind == "url_not_contains":
            return value.lower() not in page.url.lower()
        if kind == "url_contains":
            return value.lower() in page.url.lower()
        if kind == "text_present":
            return await page.get_by_text(value).count() > 0
        if kind == "selector_present":
            return await page.locator(value).count() > 0
        if kind == "api_status":
            return False  # browser flow has no token status
    except Exception:
        return False
    return False
