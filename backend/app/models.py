"""AutoQA database models (spec §23). UUIDs for externally exposed IDs."""
import enum
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


def new_uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------- Enums ----------

class Role(str, enum.Enum):
    admin = "admin"
    qa_lead = "qa_lead"
    tester = "tester"
    viewer = "viewer"


class Severity(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Priority(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class TestCategory(str, enum.Enum):
    functional = "functional"
    regression = "regression"
    ui = "ui"
    api = "api"
    integration = "integration"
    security = "security"
    performance = "performance"
    accessibility = "accessibility"
    compatibility = "compatibility"


class TestStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    blocked = "blocked"


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    completed = "completed"
    stopped = "stopped"
    failed = "failed"


class ScanStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"

class FindingStatus(str, enum.Enum):
    open = "open"
    confirmed = "confirmed"
    false_positive = "false_positive"
    fixed = "fixed"


# ---------- Core entities ----------

class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.tester)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)  # theme, notification prefs cache


class LlmSetting(Base, TimestampMixin):
    """Singleton row (id=1): UI-managed AI provider config (spec §1, §22).

    The API key is encrypted at rest with Fernet (§17) — never returned to
    the client, only a masked preview. DB values override env config.
    """
    __tablename__ = "llm_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String(20), default="none")  # none | openai | openrouter
    base_url: Mapped[str] = mapped_column(String(500), default="")
    model: Mapped[str] = mapped_column(String(200), default="auto")  # "auto" = best free model
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(50))  # run_completed | security_scan | report | a11y ...
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(500), default="")
    read_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationSetting(Base, TimestampMixin):
    __tablename__ = "notification_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    events: Mapped[dict] = mapped_column(JSON, default=dict)  # {run_completed: true, ...}
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    email_address: Mapped[str] = mapped_column(String(320), default="")


class TestEnvironment(Base, TimestampMixin):
    """Named target environment (staging, prod-like...) — spec §17."""
    __tablename__ = "test_environments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))  # staging | uat | prod-mirror
    base_url: Mapped[str] = mapped_column(String(2048), default="")
    variables: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret env vars


class TestDataset(Base, TimestampMixin):
    """Managed test data with per-environment overrides — spec §17.

    Secret values (username/password/api_key/...) are Fernet-encrypted at rest
    and masked in every API response; generation params feed faker-style
    synthesis at execution time.
    """
    __tablename__ = "test_datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="static")  # static | generated
    generator: Mapped[str] = mapped_column(String(50), default="")  # user | email | string | uuid | none
    generator_params: Mapped[dict] = mapped_column(JSON, default=dict)
    values: Mapped[dict] = mapped_column(JSON, default=dict)  # {key: value-or-encrypted}
    encrypted_keys: Mapped[list] = mapped_column(JSON, default=list)  # keys stored encrypted
    environment_id: Mapped[str | None] = mapped_column(ForeignKey("test_environments.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(String(2048))
    authorization_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Encrypted credentials (never plaintext; masked in API responses)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    owner = relationship("User")
    pages = relationship("Page", back_populates="project", cascade="all,delete-orphan")
    apis = relationship("ApiEndpoint", back_populates="project", cascade="all,delete-orphan")


class Environment(Base, TimestampMixin):
    __tablename__ = "environments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    variables_encrypted: Mapped[str] = mapped_column(Text, default="{}")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class Application(Base, TimestampMixin):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    frontend_stack: Mapped[dict] = mapped_column(JSON, default=dict)
    backend_stack: Mapped[dict] = mapped_column(JSON, default=dict)
    architecture_map: Mapped[dict] = mapped_column(JSON, default=dict)


class Page(Base, TimestampMixin):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("project_id", "url", name="uq_page_url"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500), default="")
    depth: Mapped[int] = mapped_column(Integer, default=0)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)
    project = relationship("Project", back_populates="pages")


class ApiEndpoint(Base, TimestampMixin):
    __tablename__ = "api_endpoints"
    __table_args__ = (UniqueConstraint("project_id", "method", "path", name="uq_api_method_path"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(2048))
    group: Mapped[str] = mapped_column(String(100), default="general")
    request_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    response_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    sample_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    auth_required: Mapped[bool] = mapped_column(Boolean, default=False)
    avg_response_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="observed")  # observed | openapi
    project = relationship("Project", back_populates="apis")


class Component(Base, TimestampMixin):
    __tablename__ = "components"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("pages.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(50))  # form, modal, table, ...
    name: Mapped[str] = mapped_column(String(300), default="")
    selector: Mapped[str] = mapped_column(String(500), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class TestPlan(Base, TimestampMixin):
    __tablename__ = "test_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    objective: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[dict] = mapped_column(JSON, default=dict)  # in_scope, out_of_scope
    sections: Mapped[dict] = mapped_column(JSON, default=list)  # 22 categories §6
    generated_by: Mapped[str] = mapped_column(String(30), default="heuristic")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)


class TestCase(Base, TimestampMixin):
    __tablename__ = "test_cases"
    __table_args__ = (Index("ix_tc_project_ref", "project_id", "ref"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    test_plan_id: Mapped[str | None] = mapped_column(ForeignKey("test_plans.id"), nullable=True)
    ref: Mapped[str] = mapped_column(String(60))  # TC-LOGIN-001
    scenario: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(String(200), default="general")
    category: Mapped[TestCategory] = mapped_column(Enum(TestCategory), default=TestCategory.functional)
    priority: Mapped[Priority] = mapped_column(Enum(Priority), default=Priority.medium)
    preconditions: Mapped[str] = mapped_column(Text, default="")
    test_data: Mapped[dict] = mapped_column(JSON, default=dict)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    expected_result: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="browser")  # browser | api
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    capture_on_pass: Mapped[bool] = mapped_column(Boolean, default=False)  # screenshot evidence even on PASS (§12/§19)


class TestSuite(Base, TimestampMixin):
    __tablename__ = "test_suites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))  # smoke | regression | custom
    test_case_ids: Mapped[list] = mapped_column(JSON, default=list)


class TestRun(Base, TimestampMixin):
    __tablename__ = "test_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    suite_id: Mapped[str | None] = mapped_column(ForeignKey("test_suites.id"), nullable=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.pending)
    browsers: Mapped[list] = mapped_column(JSON, default=list)  # chromium, firefox, webkit
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # retries, viewports, filters
    started_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TestExecution(Base, TimestampMixin):
    __tablename__ = "test_executions"
    __table_args__ = (Index("ix_exec_run_status", "test_run_id", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    test_run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id"), index=True)
    test_case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"), index=True)
    browser: Mapped[str] = mapped_column(String(20), default="chromium")
    status: Mapped[TestStatus] = mapped_column(Enum(TestStatus), default=TestStatus.queued)
    attempt: Mapped[int] = mapped_column(Integer, default=1)  # >1 = retry
    started_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_result: Mapped[str] = mapped_column(Text, default="")
    error_details: Mapped[dict] = mapped_column(JSON, default=dict)
    worker_id: Mapped[str] = mapped_column(String(100), default="")


class Worker(Base, TimestampMixin):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    worker_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)  # celery hostname
    kind: Mapped[str] = mapped_column(String(30), default="browser")  # browser | api | security
    status: Mapped[str] = mapped_column(String(30), default="idle")  # idle | running | offline
    current_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    browser: Mapped[str] = mapped_column(String(20), default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class Defect(Base, TimestampMixin):
    __tablename__ = "defects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("test_executions.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[Severity] = mapped_column(Enum(Severity), default=Severity.medium)
    module: Mapped[str] = mapped_column(String(200), default="")
    root_cause_ai: Mapped[str] = mapped_column(Text, default="")
    recommended_fix_ai: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="open")


class SecurityFinding(Base, TimestampMixin):
    __tablename__ = "security_findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    scan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[Severity] = mapped_column(Enum(Severity), default=Severity.medium)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    remediation: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[FindingStatus] = mapped_column(Enum(FindingStatus), default=FindingStatus.open)


class PerformanceResult(Base, TimestampMixin):
    __tablename__ = "performance_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    test_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scenario: Mapped[str] = mapped_column(String(200), default="")
    concurrent_users: Mapped[int] = mapped_column(Integer, default=1)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    p50_ms: Mapped[float] = mapped_column(Float, default=0)
    p90_ms: Mapped[float] = mapped_column(Float, default=0)
    p95_ms: Mapped[float] = mapped_column(Float, default=0)
    p99_ms: Mapped[float] = mapped_column(Float, default=0)
    throughput_rps: Mapped[float] = mapped_column(Float, default=0)
    error_rate: Mapped[float] = mapped_column(Float, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # avg latency, threshold_breached, run config (§14)


class AccessibilityResult(Base, TimestampMixin):
    __tablename__ = "accessibility_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    page_url: Mapped[str] = mapped_column(String(2048), default="")
    violations: Mapped[list] = mapped_column(JSON, default=list)
    wcag_level: Mapped[str] = mapped_column(String(10), default="AA")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # counts by rule/severity, audit summary (§15)


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30))  # screenshot | trace | video | log | report
    storage_key: Mapped[str] = mapped_column(String(500))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Report(Base, TimestampMixin):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    test_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    format: Mapped[str] = mapped_column(String(10))  # csv | xlsx | pdf | html | json
    storage_key: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # counts, size_bytes, generated scope


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50), default="")
    entity_id: Mapped[str] = mapped_column(String(36), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
