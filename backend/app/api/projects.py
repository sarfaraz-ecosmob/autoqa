"""Project endpoints (spec §24)."""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_project
from app.audit import record as audit_record
from app.db import get_db
from app.models import Project, Role, User
from app.security.crypto import decrypt_json, encrypt_json, mask_secrets
from app.security.ssrf import validate_target_url

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str
    description: str = Field(default="", max_length=2000)
    auth_type: str | None = None
    credentials: dict | None = None  # encrypted at rest, masked in responses
    authorization_confirmed: bool = False  # spec §2 step 4: explicit user attestation


def _serialize(project: Project) -> dict:
    creds = {}
    if project.credentials_encrypted:
        creds = mask_secrets(decrypt_json(project.credentials_encrypted))
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "base_url": project.base_url,
        "authorization_confirmed": project.authorization_confirmed,
        "auth_type": project.auth_type,
        "credentials": creds,  # masked
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
    if body.credentials:
        project.credentials_encrypted = encrypt_json(body.credentials)
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


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    db.delete(project)
    db.commit()
    audit_record("project.delete", user.id, "project", project.id)
