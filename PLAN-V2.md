# AutoQA — PLAN V2: from test generator to QA lifecycle platform

Companion to `new-instruction.md` (30 capability proposals). This document states,
per proposal, **what already exists in the code today**, what's missing, and a
build order grounded in the current architecture (FastAPI + Celery + Postgres,
Playwright browser-worker, pluggable LLM via OpenRouter).

Legend: ✅ implemented · 🟡 partial · ❌ not implemented

## 0. Where we are

The current platform covers **discovery → plan → cases → execution → evidence →
reports** end to end, plus several Tier-1 features. Phases 0–14 of `PLAN.md` are
complete; production hardening (Phase 15) is not.

| # | Proposal (new-instruction.md) | Status | Evidence in code |
|---|---|---|---|
| 1 | QA lifecycle framing | ✅ | Pipeline already matches stages 1–9; defect/re-test/regression exist |
| 2 | Requirements → test cases | ❌ | No Requirement model/API/UI |
| 3 | Requirement traceability matrix | ❌ | No Requirement ↔ TestCase link |
| 4 | Visual regression | ✅ | `VisualBaseline`/`VisualCheck`, pixel-diff, approve-as-baseline (tier1.py) |
| 5 | Self-healing selectors | ✅ | Fallback chain + LLM proposal, `heals[]` in evidence (browser.py) |
| 6 | Flaky detection | ✅ | `analysis/flaky.py` scoring; but **no AI cause analysis** (🟡 for causes) |
| 7 | API contract testing | 🟡 | JSON body/schema/header/time assertions exist; **no stored contract + diff engine** |
| 8 | Database validation | ❌ | No DB connector; only step-level `extract` chaining |
| 9 | Business workflow testing | 🟡 | Recorder import creates multi-step flows; no workflow model, chaining or mining |
| 10 | Test-data generation (boundary/negative corpora) | 🟡 | Static + generated datasets; no valid/invalid/boundary strategy sets |
| 11 | Auth scheme breadth | 🟡 | form login + bearer token/static/token-endpoint; missing Basic/API-key/cookie/OAuth2/OIDC/SAML |
| 12 | Role-based access matrix | ❌ | Nothing |
| 13 | Mobile/viewport profiles | 🟡 | `viewport` param on runs; no device profiles in UI |
| 14 | Accessibility | ✅ | Playwright WCAG audit per page, by-severity violations (worker_quality.py) |
| 15 | Performance | ✅ | p50–p99/throughput/error-rate, thresholds; **no per-page LCP/FCP or release diff** (🟡) |
| 16 | Security testing | ✅ | 3 tiers, isolated worker, findings model (security/scan.py) |
| 17 | Email testing | ❌ | No mailbox integration |
| 18 | SMS/OTP testing | ❌ | Nothing |
| 19 | Integration health | ❌ | No dependency probes |
| 20 | Webhook testing | 🟡 | Outbound HMAC webhooks exist; **no receiver to test *incoming* webhooks** |
| 21 | Regression intelligence (impact selection) | ❌ | Only manual refs/modules/priority/suite filters |
| 22 | CI/CD integration | ❌ | No CI API/web endpoints |
| 23 | Defect management | 🟡 | Defect model + auto-creation from failures; no external tracker sync |
| 24 | AI root-cause analysis | 🟡 | Failure summary/root-cause from one execution's evidence; no cross-source correlation |
| 25 | Synthetic monitoring | ❌ | Schedules are internal only; no external probe/availability store |
| 26 | Environment management | ✅ | TestEnvironment + per-env vars + env-scoped datasets |
| 27 | Release management | ❌ | No release entity/policy |
| 28 | QA command center | 🟡 | Per-project dashboards exist; no cross-project global dashboard |
| 29 | "Test Anything" AI interface | 🟡 | Grounded assistant answers; no action-taking generation/execution |
| 30 | Navigation evolution | 🟡 | Most sections exist; missing Requirements/Defects/API-contract/Integrations tabs |

**Bottom line:** 11 of the 30 are fully implemented, 12 partial, 7 missing. The
foundation (models, workers, evidence, LLM adapter) makes each missing piece an
increment rather than a rewrite.

## 1. Build order (7 phases)

Ordered by QA-team value vs effort, respecting dependencies. Each phase ends
green: tests, migrations auto-applied, docs updated.

### Phase V2.1 — Requirements & Traceability (proposals 2, 3) ★ highest value — ✅ DONE

The missing front door of the lifecycle. **Status: implemented & verified**
(migration 0007 live, 13 new tests, full suite 166 passed, live gate on Demo
Scan: import → generate → approve → run → matrix shows per-case results).
The detailed task checklist (Tasks 1–8) at the end of this document was
executed as written; the only deviation is that the import endpoint is split
into `POST /import` (pasted text, JSON body) and `POST /import-file`
(multipart upload) for cleaner FastAPI binding.

- **Models:** `Requirement` (external_id REQ-xxx, source BRD/FRD/SRS/Jira/manual,
  title, description, priority, status, project FK), `RequirementLink`
  (requirement ↔ test_case, coverage kind: ui/api/negative/boundary).
- **Ingest:** upload endpoint accepting Markdown, plain text, PDF, DOCX, XLSX;
  parse to candidate requirements (per heading/row/story); LLM-assisted extraction
  with heuristic fallback (deterministic when no key configured).
- **Generation:** per requirement → scenarios → cases tagged with the requirement
  id (extends `generation/testcases.py`; reuses `{{placeholder}}` + payment logic).
- **Traceability tab:** matrix requirement → cases → latest run result → defects;
  coverage stats (covered/uncovered requirements), drill-down to executions.
- **API:** `POST /requirements/import`, `GET /requirements`, `GET /traceability`.
- **Gate:** import a sample BRD on demo-app → ≥8 cases generated → matrix shows
  REQ rows with pass/fail from a real run.

### Phase V2.2 — Business Workflows (proposal 9)

- **Model:** `BusinessWorkflow` (name, steps JSON referencing pages/APIs/cases,
  expectation per step, project FK).
- **Authoring:** derive proposals from discovery (navigation chains ending in
  form/checkout actions) + recorder import; manual editor in UI.
- **Execution:** workflow runs as a single session — one browser context,
  state (cart, login) carried across steps; each step contributes to shared
  evidence; failure isolates the failing step.
- **API tests:** chain requests in one workflow with `extract` → `{{var}}` reuse
  (executor already supports extraction).
- **Gate:** e-commerce flow (login → search → add to cart → checkout) executes
  green on demo-app; failure injected at checkout shows step-scoped evidence.

### Phase V2.3 — API Contract Testing (proposal 7)

- **Model:** `ApiContract` (endpoint FK, expected status, JSON schema or example
  body, required headers, captured-at version).
- **Capture:** from OpenAPI import or from observed traffic ("pin this response
  shape as contract").
- **Engine:** validate actual responses on every API execution: missing/unexpected
  fields, type mismatches, enum changes, status drift → structured contract
  findings with severity; breaking-change detection vs previous contract.
- **UI:** APIs tab → contract column + diff viewer (expected vs actual).
- **Gate:** demo-app `/api/products` pinned, then a mutated response triggers a
  HIGH contract finding.

### Phase V2.4 — Database Validation (proposal 8)

- **Model:** `DbTarget` (name, engine postgresql/mysql/mariadb, host, port, db,
  username, password Fernet-encrypted; per-environment scoping).
- **Executor:** new step kinds `db_query` / `db_assert` inside API and browser
  cases (e.g. after POST /orders → `SELECT status FROM orders WHERE id={{order_id}}`
  → expect row exists / value matches). Read-only recommended; optional write lock.
- **Security:** credentials encrypted at rest, decrypted only in worker; queries
  parameterized; never logged, never sent to LLM; connection allowlist.
- **Drivers:** psycopg (already present), PyMySQL; mongo/sqlserver/oracle later.
- **Gate:** order-creation case on demo-app asserts the DB row via `db_assert`.

### Phase V2.5 — Auth breadth + RBAC testing (proposals 11, 12)

- **Auth schemes in auth_flow.py:** Basic (static header), API key (header or
  query), cookie/session (capture from login response), OAuth2 client-credentials
  and authorization-code (PKCE) with secure token refresh; secrets always
  Fernet-encrypted, masked in API, never sent to the LLM.
- **Auth test suite generator:** invalid credentials, expired/invalid/missing
  token, logout, session-expiry, concurrent sessions, lockout.
- **Role matrix:** `RoleProfile` (name + credentials/env override) per project;
  matrix editor (feature/module × role → expect allowed/denied); executor asserts
  navigation + API status per cell; violations reported as security findings.
- **Gate:** two demo-app roles → matrix runs → unauthorized-access violation is
  detected and reported.

### Phase V2.6 — CI/CD + Regression intelligence + Release gate (proposals 21, 22, 27)

- **CI API:** `POST /api/v1/run` (token-authed, per-project CI token): scope =
  smoke | regression | refs | modules; returns run id; `GET /api/v1/run/{id}`
  for results; exit-code semantics for pipelines (0 pass / 1 fail).
- **Webhook-out:** run-completion payload to the CI system (webhook sender exists).
- **Regression intelligence:** map executions ↔ endpoint/page (evidence already
  carries request data); given changed files/endpoints (manual list or webhook
  from CI), recommend the affected refs; always human-confirmable; never a silent
  sole gate.
- **Releases:** `Release` entity (version, run set, policy: max critical defects,
  max high security findings, perf thresholds) → computed release status
  PASS/WARN/FAIL with evidence links.
- **Gate:** GitHub Actions workflow (provided in `.github/workflows/`) calls the
  CI API on a compose instance; checkout-service change recommends checkout tests.

### Phase V2.7 — Incoming webhooks, Email/SMS, Defect sync, Command center (proposals 17, 18, 20, 23, 28)

- **Webhook receiver:** dedicated endpoint with per-target inbox; validates
  signature/headers/payload; reusable in cases as `wait_webhook` step (with
  timeout) for payment/event-driven flows.
- **Email testing:** IMAP/mailbox connector (test mailbox per environment) —
  steps `wait_email` (subject/sender/Link extraction → open reset URL);
  provider sandbox guidance for SMS/OTP (`wait_otp` via test gateway) — production
  interception out of scope by policy.
- **Defect sync:** generic connector (Jira/GitHub Issues) — user approves before
  external ticket creation; mapping config per project.
- **Command center:** global dashboard — projects, active runs, pass-rate trend,
  open defects, flaky count, security/a11y/perf summaries, AI insights strip
  (reuses grounded assistant queries).
- **Gate:** payment webhook flow passes end-to-end (receiver asserts signature +
  payload); registration flow asserts the emailed reset link works.

### Later / backlog (proposals 13, 15 enhancements, 19, 24, 25, 29, 30)

- Device profiles (mobile/tablet viewports in run dialog; orientation matrix).
- Per-page RUM-style metrics (LCP/FCP) + release-over-release perf diff.
- Integration health probes (dependency status page per project).
- Cross-source AI root cause (evidence correlation across executions + git/deploy
  metadata, presented as hypotheses, never fact).
- Synthetic monitoring (scheduled external probes, availability history) — reuses
  schedules + a new probe store; adds a monitoring dashboard.
- "Test Anything" command bar — natural-language → scoped suite generation +
  execution, built on V2.1/V2.2 generators with confirmation before running.
- Navigation reshuffle to the §30 tree (Requirements, Defects, Integrations tabs).

## 2. Suggested next step

Start with **Phase V2.1 (Requirements + Traceability)** — it unlocks the
lifecycle framing the instruction document centers on, is mostly additive
(new models + one tab), and everything later (workflows, CI selection, release
gate, AI insights) references requirements. The detailed task-by-task
implementation checklist follows.

---

## Phase V2.1 — detailed implementation checklist

Goal: upload requirements (MD/TXT/PDF/DOCX/XLSX/CSV/Jira-JSON) → parse to
requirement records → generate linked test cases → traceability matrix
(requirement → cases → latest run result → defects). All additive; no changes
to existing executors.

### Task 1 — Data model + migration — ✅

- [x] `backend/app/models.py` — add two models:
  - `Requirement`: `id` (uuid pk), `project_id` FK→projects (indexed),
    `external_id` (String(60), e.g. `REQ-AUTH-001`), `source`
    (String(20): `manual|document|jira`), `source_ref` (String(200), nullable —
    e.g. Jira key), `title` (String(300)), `description` (Text),
    `priority` (Enum Priority, default medium), `status`
    (String(20): `draft|approved|verified|blocked`, default `draft`),
    `tags` (JSON list, default []).
  - `RequirementLink`: `id` (uuid pk), `project_id` FK (indexed),
    `requirement_id` FK→requirements (indexed), `test_case_id` FK→test_cases,
    `coverage` (String(20): `ui|api|negative|boundary|security`),
    `created_at` via TimestampMixin. Unique constraint
    (`requirement_id`, `test_case_id`).
  - Table args: unique index on (`project_id`, `external_id`).
  - `Project` relationship: `requirements` (cascade all, delete-orphan).
- [x] Migration `backend/migrations/versions/0007_requirements.py` — authored
  by hand (autogenerate could not write into the mounted dir). Note:
  `postgresql.ENUM(..., create_type=False)` is required — the `priority` type
  already exists from 0001 and a plain `sa.Enum` would emit `CREATE TYPE`.
- [x] Verified: `alembic current` → `0007`; insert/query/cleanup on live Postgres.

### Task 2 — Document ingestion (`backend/app/requirements/ingest.py`, new package) — ✅

- [ ] Create `backend/app/requirements/__init__.py` and `ingest.py` with:
      `parse_document(filename: str, data: bytes) -> list[RequirementDraft]`
      (`RequirementDraft` dataclass: external_id, title, description, source,
      source_ref).
- [ ] Parsers by extension:
  - `.md` / `.txt` — split on headings (`#`/`##`) **or** blank-line sections;
    capture `REQ-[A-Z0-9-]+` tokens as external_id when present; title = first
    line (or heading), description = remainder.
  - `.json` — Jira export shape: `issues[]` → `key` (source_ref), `fields.summary`
    (title), `fields.description` (description, strip wiki markup); tolerate a
    flat list of `{external_id,title,description}` too.
  - `.csv` — header-driven: detect id/title/description columns (accept common
    aliases: id, key, ref, summary, title, description, detail).
  - `.xlsx` — first sheet, same header detection as CSV (openpyxl; **reuse the
    dependency already used by the xlsx report generator** — verify import in
    `backend/app/reports/` before adding anything to requirements.txt).
  - `.pdf` — text extraction (`pypdf`) then the `.txt` section heuristic.
  - `.docx` — `python-docx` paragraphs (headings + body), then `.txt` heuristic.
- [ ] Add `pypdf` and `python-docx` to `backend/requirements.txt` (skip if
      already present); openpyxl expected to exist.
- [ ] Heuristic post-pass `dedupe_and_normalize(drafts)`: strip empties, enforce
      title ≤300 chars, auto-number missing external_ids as `REQ-GEN-001…`.
- [ ] **Gate:** unit tests for md/txt/json/csv parsing (fixtures inline) pass.

### Task 3 — Requirement → cases generation (`backend/app/requirements/generate.py`) — ✅

- [ ] `generate_cases_for_requirement(db, project, requirement, use_llm=False)
      -> list[TestCase]`:
  - Scenario set (deterministic templates parameterized by keywords in
    title/description; mirrors `new-instruction.md` §2 example):
    1. positive main flow (`ui` or `api` kind — `api` if description contains a
       URL/path or verbs like `POST /…`),
    2. negative: invalid input (`negative`),
    3. negative: missing/empty required data (`negative`),
    4. boundary: min/max/oversized values (`boundary`),
    5. security: injection attempt in inputs (`security`),
    6. if description mentions email/otp/reset → extra expired-link + reuse
       scenarios (`negative`).
  - If `use_llm` and LLM configured: refine scenario titles/data via
    `generate_json_budgeted` (30s budget, silent fallback to the deterministic
    set — same pattern as the assistant).
  - Refs: `TC-{TAG}-001…` where TAG = external_id minus `REQ-` prefix
    (`REQ-AUTH-001` → `TC-AUTH-001…`); skip refs that already exist (idempotent
    re-runs add only new scenarios).
  - Steps: reuse executor step grammar (browser: `goto/fill/click/expect_text`;
    api: `request/status_eq/json_eq`); credentials as `{{username}}`/
    `{{password}}` placeholders — never plaintext.
  - Create `RequirementLink` per generated case with the scenario's coverage
    kind; optionally auto-create a `TestSuite` named `REQ-{external_id}`.
  - Cases are created `approved=False` — they enter the normal review gate.
- [ ] `backend/app/generation/testcases.py` untouched (discovery-based
      generation stays separate).
- [ ] **Gate:** generating twice for one requirement does not duplicate refs.

### Task 4 — API router (`backend/app/api/requirements.py`) — ✅ (import split into `/import` text + `/import-file` multipart)

- [ ] Router `APIRouter(prefix="/api/projects/{project_id}/requirements",
      tags=["requirements"])`; ownership via `get_owned_project` dependency
      (same as other routers); audit_record on mutations.
- [ ] Endpoints:
  - `POST /import` — multipart file upload (and `text`/`source_name` JSON body
    fallback) → parse → insert with `status=draft`, skip duplicate external_ids
    (report them) → `201 {imported: n, skipped: n, items: [...]}`.
  - `POST /generate-cases` — body `{requirement_ids: [...], use_llm: bool}` →
    generation for each → `{generated: n, per_requirement: {...}}`.
  - `GET ""` — list with `coverage_count` (distinct linked cases).
  - `GET /{req_id}` — detail + linked cases (ref, scenario, approved, kind).
  - `PATCH /{req_id}` — edit title/description/priority/status/tags.
  - `DELETE /{req_id}` — cascades links (cases are kept).
  - `GET /traceability` — rows per requirement: `external_id, title, status,
    cases: [{ref, scenario, kind, approved}], last_result` (latest
    TestExecution per case: pass/fail/blocked/skipped/never-run),
    `defects` (count of defects whose linked case belongs to the requirement);
    summary `{requirements, covered, uncovered, pass_rate}`.
- [ ] Register in `backend/app/main.py` (`app.include_router(requirement_routes.router)`).
- [ ] **Gate:** curl import (md + jira json) → generate → traceability returns
      rows with `never-run` results.

### Task 5 — Traceability query helpers (`backend/app/requirements/traceability.py`) — ✅

- [ ] `latest_results_per_case(db, project_id) -> dict[case_id, result]` — one
      query joining TestExecution (order by created_at desc), first row per case.
- [ ] `build_matrix(db, project) -> dict` — assemble rows + summary; keep it in
      SQL/sqlalchemy, no N+1 per requirement.
- [ ] **Gate:** unit test seeds requirement + 2 cases + 1 failed/1 passed
      execution → matrix shows PASS/FAIL correctly and `pass_rate` matches.

### Task 6 — Frontend: Requirements tab — ✅ (tsc + vite build green)

- [ ] `frontend/src/api/client.ts` — add `upload<T>(path, file: File, fields?)`
      using FormData (existing token header logic reused).
- [ ] `frontend/src/pages/ProjectDetailPage.tsx`:
  - Add `"requirements"` to `TABS` (right after `overview` — lifecycle order).
  - Interfaces: `RequirementRow`, `TraceRow`, `TraceSummary`.
  - State: `requirements`, `trace`, `reqImportState`, `reqGenBusy`, paste-text
    textarea value.
  - Tab panel (follow existing card/table markup):
    - **Import card:** file input (accept `.md,.txt,.pdf,.docx,.xlsx,.csv,.json`)
      + "or paste requirements text" textarea + Import button → shows
      imported/skipped counts.
    - **Requirements table:** external_id, title, priority chip, status, coverage
      count, row actions: *Generate cases* (single), checkbox multi-select +
      *Generate for selected*, *Delete*. Uncovered requirements get a subtle
      red "no tests" hint.
    - **Traceability matrix:** toggle button loads `/traceability`; table with
      requirement → case refs → last result chips (PASS green / FAIL red /
      never-run grey) → defect count; summary strip: coverage %, pass rate.
  - Load on tab activation like the other tabs (`if (tab === "requirements") …`).
- [ ] **Gate:** `cd frontend && npx tsc --noEmit && npx vite build --outDir
      dist-verify` green.

### Task 7 — Tests — ✅ (13 tests; full suite 166 passed / 3 skipped)

- [ ] `backend/tests/test_requirements.py`:
  - ingest: md heading split, `REQ-` token capture, jira json mapping, csv
    header aliases, docx/pdf smoke (tiny generated files), dedupe/normalize.
  - generation: deterministic scenario set per requirement type (api vs ui),
    ref naming `TC-AUTH-001`, idempotent second run, links created with correct
    coverage kinds, placeholders not plaintext.
  - API: import (multipart + text), generate-cases, list/detail/patch/delete,
    ownership enforcement (403 for other user's project), duplicate external_id
    skip.
  - traceability: matrix rows, latest-result logic, summary math, defect count.
- [ ] Run: `./scripts/dev.sh test-be` — full suite green (existing 153+
      unaffected).

### Task 8 — Live gate + docs — ✅ (live gate on Demo Scan: import → generate → approve → run → matrix results; README updated)

- [ ] On the running stack: import a sample BRD for **Demo Scan** (paste-text
      with 8–10 REQs incl. password-reset examples from `new-instruction.md`
      §2), generate cases, approve them, start a run, open the matrix → every
      REQ row shows real PASS/FAIL; screenshot for docs.
- [ ] README: new "Requirements & Traceability" section (usage + API examples)
      + tab list update in the stage guide.
- [ ] `PLAN-V2.md`: tick Phase V2.1 checkboxes; note any deviations.
- [ ] Re-run `backend/scripts/walkthrough_shots.py` later when the tab ships in
      a screenshot pass (optional).

### Order & dependencies

Tasks 1→2→3→4→5 are sequential (model → ingest → generation → API → query
helpers). Task 6 (frontend) can start after Task 4's API shape is fixed.
Task 7 runs alongside 2–5; Task 8 last. Estimated surface: ~5 new backend
files, 1 migration, 2 edited backend files, 2 edited frontend files, 1 test
file — no changes to executors, workers, or existing tables.
