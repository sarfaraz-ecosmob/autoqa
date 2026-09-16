"""Test data management API (§17) + AI assistant endpoint (§22) — Phase 14.

Test data:
- Environments: named targets with variables (staging, uat, ...)
- Datasets: static (secrets encrypted at rest) or generated (faker-style)
- API responses always mask secret values; plaintext never leaves the server
- Resolution endpoint shows the exact (masked) variable set executors receive

Assistant:
- POST /assistant/ask — grounded Q&A over real project data (§22); uses the
  configured LLM only to rephrase verified data, never to invent facts
"""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.audit import record as audit_record
from app.api.deps import get_current_user, get_owned_project
from app.db import get_db
from app.models import Project, Role, TestDataset, TestEnvironment, User
from app.security.crypto import mask_secrets
from app.testdata import (
    decrypt_dataset_values,
    encrypt_dataset_values,
    mask_dataset_values,
    resolve_variables,
)
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/projects/{project_id}", tags=["test-data"])


def _env_out(e: TestEnvironment) -> dict:
    return {
        "id": str(e.id),
        "name": e.name,
        "base_url": e.base_url,
        "variables": mask_secrets(e.variables or {}),
        "created_at": e.created_at.isoformat(),
    }


def _dataset_out(d: TestDataset) -> dict:
    return {
        "id": str(d.id),
        "name": d.name,
        "kind": d.kind,
        "generator": d.generator,
        "generator_params": d.generator_params or {},
        "values": mask_dataset_values(d.values),
        "secret_keys": d.encrypted_keys or [],
        "environment_id": str(d.environment_id) if d.environment_id else None,
        "is_active": d.is_active,
        "created_at": d.created_at.isoformat(),
    }


# ------------------------------------------------------------ environments


class EnvironmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=2048)
    variables: dict = Field(default_factory=dict)


@router.get("/environments")
def list_environments(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.execute(
        sa.select(TestEnvironment)
        .where(TestEnvironment.project_id == project.id)
        .order_by(TestEnvironment.created_at)
    ).scalars().all()
    return {"items": [_env_out(e) for e in rows]}


@router.post("/environments", status_code=201)
def create_environment(
    body: EnvironmentIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = TestEnvironment(
        project_id=project.id, name=body.name.strip(), base_url=body.base_url.strip(), variables=body.variables
    )
    db.add(row)
    db.commit()
    audit_record("testdata.environment_created", user.id, "test_environment", str(row.id), {"project": str(project.id)})
    return _env_out(row)


@router.patch("/environments/{env_id}")
def update_environment(
    env_id: str,
    body: EnvironmentIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(TestEnvironment).where(
            TestEnvironment.id == env_id, TestEnvironment.project_id == project.id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    row.name = body.name.strip()
    row.base_url = body.base_url.strip()
    # Keep masked placeholders from overwriting real values
    merged = dict(row.variables or {})
    for key, value in (body.variables or {}).items():
        if value != "••••••••":
            merged[key] = value
    row.variables = merged
    db.commit()
    return _env_out(row)


@router.delete("/environments/{env_id}", status_code=204)
def delete_environment(
    env_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(TestEnvironment).where(
            TestEnvironment.id == env_id, TestEnvironment.project_id == project.id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    linked = db.execute(
        sa.select(sa.func.count()).select_from(TestDataset).where(TestDataset.environment_id == env_id)
    ).scalar_one()
    if linked:
        raise HTTPException(status_code=409, detail="Environment has datasets linked — move them first")
    db.delete(row)
    db.commit()
    return None


# ------------------------------------------------------------ datasets


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="static", pattern="^(static|generated)$")
    generator: str = Field(default="", max_length=50)
    generator_params: dict = Field(default_factory=dict)
    values: dict = Field(default_factory=dict)
    environment_id: str | None = None
    is_active: bool = True


@router.get("/datasets")
def list_datasets(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.execute(
        sa.select(TestDataset)
        .where(TestDataset.project_id == project.id)
        .order_by(TestDataset.created_at)
    ).scalars().all()
    return {"items": [_dataset_out(d) for d in rows]}


@router.post("/datasets", status_code=201)
def create_dataset(
    body: DatasetIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.environment_id:
        env = db.execute(
            sa.select(TestEnvironment).where(
                TestEnvironment.id == body.environment_id, TestEnvironment.project_id == project.id
            )
        ).scalar_one_or_none()
        if env is None:
            raise HTTPException(status_code=404, detail="Environment not found")
    stored, encrypted_keys = (
        encrypt_dataset_values(body.values) if body.kind == "static" else (body.values, [])
    )
    row = TestDataset(
        project_id=project.id,
        name=body.name.strip(),
        kind=body.kind,
        generator=body.generator.strip(),
        generator_params=body.generator_params,
        values=stored,
        encrypted_keys=encrypted_keys,
        environment_id=body.environment_id,
        is_active=body.is_active,
    )
    db.add(row)
    db.commit()
    audit_record("testdata.dataset_created", user.id, "test_dataset", str(row.id), {"project": str(project.id), "kind": body.kind})
    return _dataset_out(row)


@router.patch("/datasets/{dataset_id}")
def update_dataset(
    dataset_id: str,
    body: DatasetIn,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(TestDataset).where(
            TestDataset.id == dataset_id, TestDataset.project_id == project.id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    row.name = body.name.strip()
    row.kind = body.kind
    row.generator = body.generator.strip()
    row.generator_params = body.generator_params
    row.environment_id = body.environment_id
    row.is_active = body.is_active

    if body.kind == "static":
        # Merge over current PLAINTEXT view, then re-encrypt: masked
        # placeholders (••••••••) preserve the stored secret unchanged.
        current = decrypt_dataset_values(row.values, row.encrypted_keys or [])
        merged = dict(current)
        for key, value in (body.values or {}).items():
            if value != "••••••••":
                merged[key] = value
        stored, encrypted_keys = encrypt_dataset_values(merged)
        row.values = stored
        row.encrypted_keys = encrypted_keys
    else:
        row.values = body.values
        row.encrypted_keys = []
    db.commit()
    return _dataset_out(row)


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.execute(
        sa.select(TestDataset).where(
            TestDataset.id == dataset_id, TestDataset.project_id == project.id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    db.delete(row)
    db.commit()
    return None


# ------------------------------------------------------------ resolution preview


@router.get("/test-data/resolve")
def resolve_test_data(
    environment_id: str | None = None,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The exact variable set executors receive — secrets masked."""
    variables = resolve_variables(project, environment_id, db=db)
    return {"variables": mask_secrets(variables), "count": len(variables)}


# ------------------------------------------------------------ assistant (§22)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@router.post("/assistant/ask")
def assistant_ask(
    body: AskIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
):
    from app.ai.assistant import answer_question

    result = answer_question(str(project.id), body.question)
    audit_record("assistant.ask", user.id, "project", str(project.id), {"intent": result.get("intent")})
    return result
