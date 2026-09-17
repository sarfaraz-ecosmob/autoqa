"""Authenticated-target support (spec §9/§2): how AutoQA tests apps behind a login.

Three user-provided pieces (all optional):
  credentials   → dict like {"username": "qa@corp.com", "password": "..."} stored
                  Fernet-encrypted on the Project, masked in every API response.
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
}


def normalize_auth(auth: dict | None) -> dict:
    """Merge user-provided auth flow config over defaults; add flags."""
    merged = dict(_DEFAULT)
    merged.update({k: v for k, v in (auth or {}).items() if v is not None})
    merged["has_credentials"] = True
    return merged


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
        page.wait_for_timeout(500)  # let the app settle after submit
    except Exception as exc:  # noqa: BLE001
        log.append({"action": "auth_login", "result": "fail", "error": f"form submit failed: {exc}"[:200]})
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
    sub = {**variables, **creds}
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
