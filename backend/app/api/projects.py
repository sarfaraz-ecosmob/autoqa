"""Project endpoints (spec §24)."""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
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
from app.security.crypto import decrypt_json, encrypt_json, mask_secrets
from app.security.ssrf import validate_target_url

router = APIRouter(prefix="/api/projects", tags=["projects"])


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
    half is not being updated."""
    from app.testdata import decrypt_credentials

    existing_values, existing_auth = decrypt_credentials(current or "")
    new_values = values if values is not None else existing_values
    new_auth = auth if auth is not None else existing_auth
    if not new_values and not new_auth:
        return None
    return encrypt_json({"values": new_values, "auth": new_auth})


def _serialize(project: Project) -> dict:
    from app.testdata import decrypt_credentials

    values, auth = decrypt_credentials(project.credentials_encrypted or "")
    creds = mask_secrets(values) if values else {}
    # Auth flow config is not secret (selectors/URLs) but mask any embedded
    # header values that could carry tokens.
    auth_safe = mask_secrets(auth) if auth else {}
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "base_url": project.base_url,
        "authorization_confirmed": project.authorization_confirmed,
        "auth_type": project.auth_type,
        "credentials": creds,  # masked
        "has_credentials": bool(values),
        "auth": auth_safe,  # login flow config (masked)
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
# schema and without an ORM relationship cascade. `db.delete(project)` only
# removes the three related collections (pages/apis/requirements) and then
# fails with a ForeignKeyViolation as soon as any other child row exists
# (e.g. an `applications` row after the first analysis run). Child rows are
# therefore deleted explicitly, children-first, before the project itself.
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
    TestPlan,               # after cases
    TestSuite,              # after runs
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
