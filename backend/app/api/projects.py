"""Project endpoints (spec §24)."""
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.tasks import celery_app
from app.models import (
    AccessibilityResult,
    ApiEndpoint,
    Application,
    Component,
    Defect,
    Environment,
    Notification,
    Page,
    PerformanceResult,
    Project,
    Report,
    Requirement,
    RequirementLink,
    Role,
    SecurityFinding,
    TestCase,
    TestDataset,
    TestEnvironment,
    TestExecution,
    TestPlan,
    TestRun,
    TestSchedule,
    TestSuite,
    User,
    VisualBaseline,
    VisualCheck,
    Webhook,
)
from app.execution.auth_flow import resolve_api_auth_headers
from app.security.crypto import decrypt_json, encrypt_json, mask_secret, mask_secrets
from app.security.ssrf import validate_target_url

router = APIRouter(prefix="/api/projects", tags=["projects"])

_MASK_PLACEHOLDER = "••••••••"


def _mask_auth(auth: dict) -> dict:
    """Mask only credential-like auth fields (e.g. a static header value that
    may carry a token). Login URL, selectors and success checks stay readable
    so the UI can display and re-save them intact."""
    from app.security.crypto import mask_secrets

    safe: dict = {}
    for k, v in auth.items():
        if isinstance(v, dict):
            safe[k] = mask_secrets(v)  # e.g. api_token_request body
        else:
            lowered = k.lower()
            # Only static token-ish values are masked; selector/URL/assert
            # keys contain "selector", "url", "assert", "header", "prefix",
            # "path" and must survive a round-trip untouched.
            if any(s in lowered for s in ("token", "secret", "header_value")) and "selector" not in lowered and "url" not in lowered and "path" not in lowered:
                safe[k] = _MASK_PLACEHOLDER
            else:
                safe[k] = v
    return safe


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str
    description: str = Field(default="", max_length=2000)
    auth_type: str | None = None
    credentials: dict | None = None  # encrypted at rest, masked in responses
    auth: dict | None = None  # auth flow config (login selectors, API auth header)
    authorization_confirmed: bool = False  # spec §2 step 4: explicit user attestation


class ProjectUpdate(BaseModel):
    """PATCH body. `credentials` here means the credential VALUES dict
    (username/password/token/…); `auth` describes HOW to authenticate
    (login flow selectors, API auth header, token request)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    auth_type: str | None = Field(default=None, max_length=50)
    credentials: dict | None = None
    auth: dict | None = None  # auth flow config; encrypted together with values
    clear_credentials: bool | None = None


def _store_credentials(current: str | None, values: dict | None, auth: dict | None) -> str | None:
    """Merge values + auth into one encrypted blob, preserving whichever
    half is not being updated. An empty values dict means "don't change the
    saved values" (auth-only saves from the UI must not wipe credentials).
    Values merge per key: omitted keys and mask placeholders (••••••••) keep
    the stored value, so a partial save — or a round-trip of masked output
    that still carries the unmasked username — can never wipe the other
    secrets (password, otp_secret, …). Full removal is clear_credentials.
    The stored format is always {"values": dict, "auth": dict} — never None —
    so decrypt_credentials() doesn't misread it as the legacy flat format."""
    from app.testdata import decrypt_credentials

    existing_values, existing_auth = decrypt_credentials(current or "")
    new_values = values
    new_auth = auth if auth is not None else existing_auth
    # Defensive: a client that round-tripped masked output (e.g. password_selector
    # rendered as ••••••••) must not overwrite the real stored config — treat
    # mask placeholders as "no change" (per key).
    if isinstance(new_values, dict):
        incoming = {k: v for k, v in new_values.items() if v != _MASK_PLACEHOLDER}
        new_values = {**existing_values, **incoming}
    if isinstance(new_auth, dict):
        cleaned_auth: dict = {}
        for k, v in new_auth.items():
            if isinstance(v, dict):
                cleaned_auth[k] = {
                    ik: existing_auth.get(k, {}).get(ik, iv)
                    for ik, iv in v.items()
                    if iv != _MASK_PLACEHOLDER
                }
            else:
                if v != _MASK_PLACEHOLDER:
                    cleaned_auth[k] = v
        new_auth = cleaned_auth
    if not new_values and not new_auth:
        return None
    return encrypt_json({"values": new_values or {}, "auth": new_auth or {}})


def _serialize(project: Project) -> dict:
    from app.testdata import decrypt_credentials

    values, auth = decrypt_credentials(project.credentials_encrypted or "")
    creds = mask_secrets(values) if values else {}
    # Auth flow config is NOT secret (login URL, selectors) — masking it here
    # used to corrupt re-saves (password_selector came back as •••••••• and
    # the UI stored that placeholder). Only mask embedded header values.
    auth_safe = _mask_auth(auth) if auth else {}
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "base_url": project.base_url,
        "authorization_confirmed": project.authorization_confirmed,
        "auth_type": project.auth_type,
        "credentials": creds,  # masked
        "has_credentials": bool(values),
        "auth": auth_safe,  # login flow config (non-secret fields in clear)
        "created_at": project.created_at.isoformat(),
    }


@router.post("", status_code=201)
def create_project(
    body: ProjectIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        validate_target_url(body.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    project = Project(
        owner_id=user.id,
        name=body.name,
        description=body.description,
        base_url=body.base_url,
        auth_type=body.auth_type,
        authorization_confirmed=body.authorization_confirmed,
        settings={},
    )
    if body.credentials or body.auth:
        project.credentials_encrypted = _store_credentials(None, body.credentials, body.auth)
    db.add(project)
    db.commit()
    audit_record(
        "project.create",
        user.id,
        "project",
        project.id,
        {"name": body.name, "authorization_confirmed": body.authorization_confirmed},
    )
    return _serialize(project)


@router.get("")
def list_projects(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    stmt = sa.select(Project).order_by(Project.created_at.desc())
    if user.role != Role.admin:
        stmt = stmt.where(Project.owner_id == user.id)
    return [_serialize(p) for p in db.execute(stmt).scalars()]


@router.get("/{project_id}")
def get_project(project: Project = Depends(get_owned_project)) -> dict:
    return _serialize(project)


@router.get("/{project_id}/credentials")
def get_credentials(
    reveal: bool = False,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    """Saved credentials + auth flow. Masked by default; `?reveal=true`
    decrypts for the owner's show/hide toggle (audited, owner/admin only via
    get_owned_project)."""
    from app.testdata import decrypt_credentials

    values, auth = decrypt_credentials(project.credentials_encrypted or "")
    if reveal:
        audit_record("credentials.reveal", user.id, "project", project.id)
        return {
            "credentials": values,
            "auth": auth,
            "has_credentials": bool(values),
        }
    return {
        "credentials": mask_secrets(values) if values else {},
        "auth": _mask_auth(auth) if auth else {},
        "has_credentials": bool(values),
    }


@router.post("/{project_id}/test-sign-in", status_code=202)
def test_sign_in(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Try the saved login flow in a real browser (async; poll /status)."""
    from app.testdata import decrypt_credentials

    values, auth = decrypt_credentials(project.credentials_encrypted or "")
    if not auth.get("login_url"):
        raise HTTPException(422, "Save a login page URL first")
    if not values:
        raise HTTPException(422, "Save credentials first")

    task_id = uuid.uuid4().hex
    celery_app.send_task(
        "app.test_project_login",
        kwargs={"project_id": project.id},
        task_id=task_id,
    )
    audit_record("auth.test_signin", user.id, "project", project.id, {"task_id": task_id})
    return {"task_id": task_id, "status": "started"}


@router.get("/{project_id}/test-sign-in/status")
def test_sign_in_status(
    task_id: str,
    project: Project = Depends(get_owned_project),
) -> dict:
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    response: dict = {"task_id": task_id, "status": result.state}
    if result.ready():
        try:
            response["result"] = result.result
        except Exception:
            response["result"] = None
    return response


class DetectSelectorsIn(BaseModel):
    login_url: str = Field(min_length=1)


class DryRunIn(BaseModel):
    """Current form values (unsaved edits allowed) — used as overrides on top
    of the saved project config. Nothing is persisted by a dry run."""

    login_url: str
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    success_assert: str | None = None
    username: str | None = None
    password: str | None = None
    otp_secret: str | None = None  # base32 TOTP secret (2FA)
    otp_code: str | None = None  # static/backdoor one-time code
    otp_selector: str | None = None
    otp_submit_selector: str | None = None


class TestApiAuthIn(BaseModel):
    """Sync API-auth check: token request or static header against the target.
    Values come straight from the form (overrides), falling back to saved."""

    token_endpoint: str | None = None
    token_path: str = "token"
    api_auth_header: str = "Authorization"
    api_auth_prefix: str = "Bearer "
    api_auth_header_value: str | None = None
    username: str | None = None
    password: str | None = None
    otp_secret: str | None = None  # enables {{otp}} in the token request body
    otp_code: str | None = None


@router.post("/{project_id}/detect-login", status_code=202)
def start_detect_login(
    body: DetectSelectorsIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    """Auto-detect login form selectors on the given page (async task)."""
    task_id = uuid.uuid4().hex
    celery_app.send_task(
        "app.detect_login_selectors",
        kwargs={"project_id": project.id, "login_url": body.login_url},
        task_id=task_id,
    )
    audit_record("auth.detect_selectors", user.id, "project", project.id, {"login_url": body.login_url})
    return {"task_id": task_id, "status": "started"}


@router.get("/{project_id}/detect-login/status")
def detect_login_status(
    task_id: str,
    project: Project = Depends(get_owned_project),
) -> dict:
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    response: dict = {"task_id": task_id, "status": result.state}
    if result.ready():
        try:
            response["result"] = result.result
        except Exception:
            response["result"] = None
    return response


@router.post("/{project_id}/dry-run-login", status_code=202)
def start_dry_run_login(
    body: DryRunIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    """Dry-run the login flow with the form's current values (async task)."""
    task_id = uuid.uuid4().hex
    celery_app.send_task(
        "app.dry_run_login",
        kwargs={
            "project_id": project.id,
            "auth_override": {
                "login_url": body.login_url,
                "username_selector": body.username_selector,
                "password_selector": body.password_selector,
                "submit_selector": body.submit_selector,
                "success_assert": body.success_assert,
                "otp_selector": body.otp_selector,
                "otp_submit_selector": body.otp_submit_selector,
            },
            "credentials_override": {
                k: v
                for k, v in {
                    "username": body.username,
                    "password": body.password,
                    "otp_secret": body.otp_secret,
                    "otp_code": body.otp_code,
                }.items()
                if v
            },
        },
        task_id=task_id,
    )
    audit_record("auth.dry_run", user.id, "project", project.id, {"login_url": body.login_url})
    return {"task_id": task_id, "status": "started"}


@router.get("/{project_id}/dry-run-login/status")
def dry_run_login_status(
    task_id: str,
    project: Project = Depends(get_owned_project),
) -> dict:
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    response: dict = {"task_id": task_id, "status": result.state}
    if result.ready():
        try:
            response["result"] = result.result
        except Exception:
            response["result"] = None
    return response


@router.post("/{project_id}/test-api-auth")
def test_api_auth(
    body: TestApiAuthIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
) -> dict:
    """Validate the API auth setup against the live target — synchronous.

    Precedence mirrors the executor: static header value → token request →
    none. Only the final header name/value (masked) and outcome are returned;
    secrets never appear in the response or logs."""
    from app.testdata import decrypt_credentials

    values, saved_auth = decrypt_credentials(project.credentials_encrypted or "")

    # Form overrides win; fall back to the saved blob.
    api_token_request = None
    if body.token_endpoint:
        api_token_request = {
            "method": "POST",
            "path": body.token_endpoint,
            "json": {"username": "{{username}}", "password": "{{password}}"},
            "token_path": body.token_path or "token",
        }
    elif (saved_auth.get("api_token_request") or {}).get("path"):
        api_token_request = saved_auth["api_token_request"]

    auth: dict = {
        "api_auth_header": body.api_auth_header or saved_auth.get("api_auth_header") or "Authorization",
        "api_auth_prefix": body.api_auth_prefix if body.api_auth_prefix is not None else (saved_auth.get("api_auth_prefix") or "Bearer "),
        "api_auth_header_value": body.api_auth_header_value or saved_auth.get("api_auth_header_value") or "",
    }
    if api_token_request:
        auth["api_token_request"] = api_token_request

    credentials = {
        "username": body.username or values.get("username", ""),
        "password": body.password or values.get("password", ""),
    }
    # 2FA: form overrides win, fall back to the saved secret/code so {{otp}}
    # in the token request resolves during the live check too.
    otp_secret = body.otp_secret or values.get("otp_secret", "")
    otp_code = body.otp_code or values.get("otp_code", "")
    if otp_secret:
        credentials["otp_secret"] = otp_secret
    if otp_code:
        credentials["otp_code"] = otp_code

    log: list = []
    headers = resolve_api_auth_headers(auth, credentials, dict(credentials), project.base_url, log)
    token_fetch_ok = any(e.get("action") == "auth_token_fetch" and e.get("result") == "pass" for e in log)

    if not headers:
        return {
            "ok": False,
            "message": (
                "No API auth to test — paste a static token, fill the token endpoint, "
                "or clear the header name to disable API auth."
            ),
            "token_fetch_ok": False,
        }

    # Replay the header against the target exactly as API tests would.
    masked = mask_secret(list(headers.values())[0])
    try:
        base = validate_target_url(project.base_url)
    except ValueError as exc:
        return {"ok": False, "message": f"base_url rejected: {exc}", "token_fetch_ok": token_fetch_ok}

    import httpx

    try:
        resp = httpx.get(base.rstrip("/") + "/", headers=headers, timeout=15.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "message": f"Could not reach {base} — {str(exc)[:150]}",
            "token_fetch_ok": token_fetch_ok,
            "header": {"name": list(headers.keys())[0], "value_masked": masked},
        }

    reachable = resp.status_code < 500
    return {
        "ok": reachable,
        "message": (
            f"Dry run successful — sent {list(headers.keys())[0]}: {masked} to {base} "
            f"and got HTTP {resp.status_code}."
            if reachable
            else f"Target answered HTTP {resp.status_code} — header sent, but the endpoint is erroring."
        ),
        "token_fetch_ok": token_fetch_ok,
        "http_status": resp.status_code,
        "header": {"name": list(headers.keys())[0], "value_masked": masked},
    }


@router.patch("/{project_id}")
def update_project(
    body: ProjectUpdate,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Update project settings — including test credentials (encrypted at
    rest, Fernet §17) and the auth flow used by scans/tests/API cases."""
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.auth_type is not None:
        project.auth_type = body.auth_type
    if body.clear_credentials:
        project.credentials_encrypted = None
    elif body.credentials is not None or body.auth is not None:
        project.credentials_encrypted = _store_credentials(
            project.credentials_encrypted, body.credentials, body.auth
        )
    db.commit()
    audit_record(
        "project.update",
        user.id,
        "project",
        project.id,
        {"credentials_set": body.credentials is not None, "auth_set": body.auth is not None,
         "cleared": bool(body.clear_credentials)},
    )
    return _serialize(project)


@router.patch("/{project_id}/authorization")
def confirm_authorization(
    confirmed: bool,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Spec §2 Step 4: explicit authorization confirmation before testing."""
    project.authorization_confirmed = confirmed
    db.commit()
    audit_record(
        "project.authorization", user.id, "project", project.id, {"confirmed": confirmed}
    )
    return _serialize(project)


# Tables referencing projects.id without an ON DELETE CASCADE in the DB
# schema and mostly without an ORM relationship cascade. `db.delete(project)`
# only removes the three related collections (pages/apis/requirements) and then
# fails with a ForeignKeyViolation as soon as any other child row exists
# (e.g. an `applications` row after the first analysis run). Child rows are
# therefore deleted explicitly, children before parents.
_PROJECT_CHILD_ORDER = (
    TestRun,                # after executions
    TestSchedule,           # → test_environments
    TestDataset,            # → test_environments
    TestEnvironment,
    RequirementLink,        # → requirements, test_cases
    TestCase,               # → test_plans
    TestSuite,              # → test_plans (test_plan_id pin) — before plans
    TestPlan,               # after cases + suites
    Component,              # → pages
    Page,
    ApiEndpoint,
    Requirement,            # after links
    # independent project children
    Report,
    PerformanceResult,
    AccessibilityResult,
    SecurityFinding,
    Environment,
    Application,
    Webhook,
)


def _delete_project_cascade(db: Session, project_id: str) -> None:
    """Bulk-delete every project-owned child row (children first), then the
    project. Bulk sa.delete() keeps this fast for data-heavy projects."""
    # Grandchildren whose FKs point at other child rows must go first.
    db.execute(sa.delete(Defect).where(Defect.project_id == project_id))  # → executions
    db.execute(sa.delete(VisualCheck).where(VisualCheck.project_id == project_id))  # → cases
    db.execute(sa.delete(VisualBaseline).where(VisualBaseline.project_id == project_id))
    # Suites reference test_plans (plan pin) — clear the pin first so plan
    # deletion can't violate the FK regardless of delete order.
    db.execute(
        sa.update(TestSuite)
        .where(
            TestSuite.project_id == project_id,
            TestSuite.test_plan_id.isnot(None),
        )
        .values(test_plan_id=None)
    )
    # test_executions has no project_id column — reach it through its run.
    db.execute(
        sa.delete(TestExecution).where(
            TestExecution.test_run_id.in_(
                sa.select(TestRun.id).where(TestRun.project_id == project_id)
            )
        )
    )
    for model in _PROJECT_CHILD_ORDER:
        db.execute(sa.delete(model).where(model.project_id == project_id))
    db.execute(sa.delete(Project).where(Project.id == project_id))


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _delete_project_cascade(db, project.id)
    db.commit()
    audit_record("project.delete", user.id, "project", project.id)
