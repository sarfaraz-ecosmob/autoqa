# AutoQA — Phase-wise Implementation Plan

Derived from `instructions.md` (autonomous QA platform: discovery → analysis → test generation → execution → real-time orchestration → reporting).

## Guiding principles

1. **Docker from day one.** Every phase is built, tested, and verified inside Docker Compose — not on the host. No phase is "done" until `docker compose up` brings up the full stack and its acceptance check passes.
2. **Build incrementally.** Follow the spec's §32: never proceed with known-broken functionality. Each phase ends with tests, migrations, and integration validation.
3. **Security is not a phase — it's a gate.** SSRF protection, secret handling, and authorization confirmations are wired in from Phase 1 and hardened continuously (§25).
4. **MVP path first.** The critical flow is: *URL → Authorization → Crawl → Architecture Discovery → Test Plan → AI Test Cases → User Approval → Playwright/API Execution → Real-time Dashboard → Evidence → AI Failure Analysis → Reports* (§32, "One important improvement").

## Target stack (per §1, §27)

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript + Vite + Tailwind CSS, WebSocket/SSE live updates |
| Backend | Python FastAPI (REST + WebSocket), async |
| Workers | Celery/Redis workers; Playwright browser workers; API/security/performance workers |
| Database | PostgreSQL (UUID external IDs, Alembic migrations) |
| Cache/Queue | Redis |
| Browsers | Playwright — Chromium, Firefox, WebKit |
| Storage | S3-compatible object storage (MinIO locally) for screenshots, traces, videos, reports |
| AI | Pluggable LLM adapter (OpenAI-compatible + locally hosted); never hard-coded to one provider |
| Edge | Nginx reverse proxy |
| Infra | Docker Compose (dev & prod) → CI/CD |

## Compose services (§27) — defined in Phase 1, filled in across phases

`nginx` · `frontend` · `backend` · `postgres` · `redis` · `minio` (object storage) · `worker` · `browser-worker` · `scheduler` · `demo-app` (spec §30 requires a default test target)

## Repository layout (§33)

```
/frontend    /backend    /workers    /ai    /database    /reports
/docker      /tests      /docs       /scripts
```

---

## Phase 0 — Scaffolding & Docker skeleton

**Goal:** Empty-but-running stack. Every service in `docker-compose.yml` starts healthy.

- [ ] Repo layout per §33; `README.md`, `.env.example` (no secrets committed)
- [ ] `docker-compose.yml` with all services, healthchecks, networks (public / worker-isolated), volumes
- [ ] Dockerfiles: multi-stage, non-root, pinned bases (§28 non-root requirement)
- [ ] `/docker` nginx config proxying `/` → frontend, `/api` + `/ws` → backend
- [ ] Backend "hello" endpoint; frontend "hello" page; worker heartbeat task
- [ ] `Makefile` / `scripts/` for `up`, `down`, `logs`, `migrate`, `test`
- [ ] **Docker gate:** `docker compose up` → all services healthy; nginx serves frontend; nginx proxies to backend; worker consumes a Redis ping task

## Phase 1 — Architecture, database schema & authentication (spec §32 Phase 1, §23, §25)

**Goal:** Production-grade foundation: DB models, migrations, auth, RBAC, security primitives.

- [ ] PostgreSQL schema for all §23 entities: Users, Projects, Environments, Applications, Pages, APIs, Components, TestPlans, TestCases, TestSuites, TestRuns, TestExecutions, Workers, Defects, SecurityFindings, PerformanceResults, AccessibilityResults, Artifacts, Reports, AuditLogs
- [ ] Alembic migrations; UUID primary keys for externally exposed IDs
- [ ] JWT auth: register/login/refresh; secure password hashing (argon2/bcrypt)
- [ ] RBAC + project-level authorization middleware
- [ ] Secret encryption at rest (Fernet/AES); secret-masking utilities (§17)
- [ ] **SSRF guard module** (§25): URL allowlisting; block localhost, 127.0.0.0/8, RFC1918, link-local, cloud metadata (169.254.169.254), internal DNS — used by *every* future URL fetch/crawl
- [ ] Input validation (Pydantic), rate limiting, audit logging on auth + project mutations
- [ ] MinIO service wired; artifact bucket creation on startup
- [ ] Pluggable LLM adapter interface in `/ai` (OpenAI-compatible + local) — interface only
- [ ] **Docker gate:** authenticated request flow through nginx→backend→Postgres works in Compose; migrations run via `docker compose run backend alembic upgrade head`; unit + API tests pass inside containers (`/tests`); Prometheus `/metrics` + healthchecks live (§26)

## Phase 2 — Website discovery / crawler (§3)

**Goal:** Controlled crawl producing a stored sitemap and component inventory.

- [ ] Playwright-based crawler in `browser-worker`: pages, navigation, forms, buttons, inputs, dropdowns, checkboxes, radios, modals, tables, search, file upload/download, auth pages, registration, password reset, profile, checkout, error pages
- [ ] Network interception during crawl → capture API requests, JS/CSS/static resources (feeds Phase 3)
- [ ] Modern-app coverage per the "important improvement": browser crawling **+** DOM analysis **+** network interception **+** JS analysis (SPA-aware — don't rely on link scraping alone)
- [ ] Crawl safety controls: same-origin restriction, max depth, max URL count, rate limiting, dedupe, robots.txt handling, session isolation, timeouts — all configurable per project
- [ ] Store Pages + Components; render sitemap tree in UI (§3 example tree)
- [ ] Scan REST endpoints (`POST /projects/{id}/scan`, `GET /projects/{id}/scan/status`) with Celery job + progress events over WebSocket
- [ ] Authorization confirmation step enforced before any scan starts (§2 Step 4)
- [ ] **Docker gate:** scan the `demo-app` from inside Compose → sitemap persisted, viewable in UI, scan status streams live; SSRF guard blocks a malicious URL e2e

## Phase 3 — Frontend / backend / API discovery (§4, §5)

**Goal:** Architecture map + API inventory from crawl evidence and optional OpenAPI import.

- [ ] Frontend fingerprinting with evidence thresholds: React, Angular, Vue, Next.js, Nuxt, Svelte, WordPress, static HTML; detect bundles, CSS frameworks, client routes, storage, cookies, service workers
- [ ] No weak-fingerprint claims — only report technologies with supporting evidence (§4)
- [ ] Backend discovery from observed traffic: REST, GraphQL, WebSocket, auth/CRUD/upload/download/health/error endpoints
- [ ] API recorder: method, URL, headers, query/path params, request/response schema, auth requirements, response time, errors; auto-group by function (Auth/User/Product/Order/Payment/Reporting APIs)
- [ ] OpenAPI/Swagger import + analysis when provided (§2 Step 3)
- [ ] Architecture map model + UI view (Browser → Frontend → Gateway → APIs → DB → External)
- [ ] **Docker gate:** crawl of `demo-app` yields detected frontend stack + grouped API list in UI; OpenAPI import for the demo app populates the API inventory

## Phase 4 — Test plan generation (§6)

**Goal:** AI-generated, user-editable test plan covering all 22 categories with priorities.

- [ ] LLM pipeline: discovery context (sitemap, architecture, APIs) → structured test plan (objective, scope, out-of-scope, components, all §6 categories)
- [ ] Priority assignment (Critical/High/Medium/Low) + category tags (Functional/Regression/UI/API/Integration/Security/Performance/Accessibility/Compatibility)
- [ ] TestPlan persistence + versioning; `POST/GET /projects/{id}/test-plan`
- [ ] UI: view/edit/approve plan; graceful fallback (heuristic plan) when no LLM is configured
- [ ] **Docker gate:** one-click plan generation on `demo-app` project in Compose; plan renders in UI with all categories

## Phase 5 — AI test-case generation & review workflow (§7, §8)

**Goal:** Detailed reviewable test cases with full approval workflow before execution.

- [ ] Generator producing every §7 field: ID, scenario, module, category, priority, preconditions, test data, steps, expected/actual result, status, execution time, evidence, error details
- [ ] Positive **and** negative cases (TC-LOGIN-001 style); seed test data from discovery + user-provided data
- [ ] Review UI: approve / reject / edit / duplicate / disable / change priority / change test data / modify expected result
- [ ] "Approve all" + "Approve selected"; only approved cases become executable
- [ ] TestSuites: smoke + regression auto-tagging (feeds §18 filtering)
- [ ] **Docker gate:** generate → review → approve flow e2e in Compose; rejected/edited cases persist correctly

## Phase 6 — Execution engine: Playwright + API (§9, §18)

**Goal:** Real execution of approved cases with control, retries, and cross-browser support.

- [ ] Celery worker pool executing browser cases via Playwright (Chromium/Firefox/WebKit; desktop + mobile viewports)
- [ ] API execution engine: GET/POST/PUT/PATCH/DELETE/GraphQL; validations — status, body, JSON schema, headers, response time, business rules
- [ ] TestRun lifecycle: `POST /test-runs`, `start/pause/resume/stop`; run selected / by module / by priority / smoke / regression
- [ ] Configurable retry policy (e.g., max retries = 2) with original-vs-retry outcomes clearly distinguished
- [ ] Execution records per step with timing; artifacts (screenshots, traces, video) uploaded to MinIO
- [ ] Celery `scheduler` service for run orchestration/queueing
- [ ] **Docker gate:** full run against `demo-app` inside Compose across ≥2 browsers; pause/resume/stop verified; retries tagged; artifacts retrievable from MinIO

## Phase 7 — Real-time dashboard & worker visibility (§10, §11, §20, §31)

**Goal:** The flagship live UI: counters, progress, logs, worker status — no refresh needed.

- [ ] WebSocket (with SSE fallback) through nginx: run progress, live logs, worker heartbeats
- [ ] Counters: Total / Running / Passed / Failed / Skipped / Blocked / Queued + progress bar
- [ ] Live log viewer (timestamped step logs); execution timeline
- [ ] Worker panel: ID, status, current test, queue size, elapsed, CPU/mem, browser, environment
- [ ] Enterprise shell per §31/§20: sidebar nav, dashboard cards, data tables (search/filter/sort/paginate), test detail drawer, charts (trend, pass/fail ratio, severity, module failures, browser matrix, duration, API latency), dark/light themes
- [ ] Prometheus metrics wired to UI (§26 metric names)
- [ ] **Docker gate:** two browsers viewing the same run see identical live updates through nginx in Compose; metrics scraped

## Phase 8 — Failure analysis & evidence collection (§12)

**Goal:** Automatic evidence bundles + AI failure triage (evidence-backed, never stated as fact).

- [ ] On failure collect: screenshot, console logs, network logs, request/response, stack trace, DOM snapshot, Playwright trace, video (when enabled), API response, execution logs
- [ ] AI analysis → Failure Summary, Possible Root Cause, Affected Component, Severity, Recommended Fix — always labeled as possible, tied to evidence (§12)
- [ ] Failure evidence viewer in UI (drawer/tabbed)
- [ ] Defect records created from failures; severity workflow
- [ ] Retry-failed action from the run view (§18)
- [ ] **Docker gate:** deliberately broken `demo-app` scenario → full evidence bundle viewable; AI analysis rendered with evidence links

## Phase 9 — API testing depth (§5, §9)

**Goal:** First-class API suite from discovered/imported specs.

- [ ] AI generation of API test cases from recorded OpenAPI/observed traffic (auth, CRUD, negative, schema)
- [ ] Collection-level execution (auth token flows, chained requests, environment variables)
- [ ] Schema validation + response-time assertions; API latency metrics
- [ ] **Docker gate:** API-only run against `demo-app` backend passes/fails correctly in Compose

## Phase 10 — Security testing module (§13, §25)

**Goal:** Tiered security scanning behind explicit authorization.

- [ ] Three tiers: **Passive Scan** (default, safe) → **Safe Active Scan** → **Authorized Full Scan** (explicit re-confirmation)
- [ ] Checks per §13: auth/authz weaknesses, IDOR/BOLA, session issues, CSRF, XSS, SQLi/command-injection *indicators*, SSRF indicators, path traversal, security headers, cookie flags, CORS, sensitive exposure, rate limiting, password policy, error handling
- [ ] Hard safeguards: no destructive payloads by default, never against production, isolated security worker, strict payload allowlist, egress controls
- [ ] SecurityFindings model + dedicated UI section with severity; optional scanner integration in isolated workers
- [ ] **Docker gate:** passive + safe active scan on `demo-app` runs in isolated worker; findings persisted + displayed; full scan requires explicit confirmation flow

## Phase 11 — Accessibility, performance & cross-browser matrix (§14, §15, §16)

**Goal:** Optional quality modules feeding the report.

- [x] Accessibility via Playwright (built-in WCAG audit — labels, alt text, headings, lang, zoom, tabindex; axe-core integration can layer on later): separate result stream
- [x] Performance module: user-defined VUs/RPS/duration/ramp-up/max response time; collect p50/p90/p95/p99, throughput, error rate; guardrails for production targets (hard request cap, UI-locked to smoke scale)
- [x] Cross-browser matrix UI: Test Case × Chromium/Firefox/WebKit → PASS/FAIL/SKIPPED (per-run + project-wide)
- [x] **Docker gate:** a11y + smoke perf run against `demo-app` in Compose; matrix renders across engines; screenshot evidence verified cross-container (browser-worker → shared artifacts volume → API)

## Phase 12 — Reporting (§19)

**Goal:** Downloadable, professional reports in all formats.

- [x] Generators: CSV, Excel, PDF, HTML, JSON — stored via the artifact storage layer (shared volume / MinIO), downloadable from the dashboard
- [x] Content per §19: executive summary, environment, app info, test plan, cases, execution summary, stats, failed tests, defects, security/performance/accessibility results, screenshots/evidence (HTML embeds base64 PNGs; PDF embeds validated images), recommendations — all evidence-grounded
- [x] Report generation as background job with status (`reports` Celery queue; Report.status pending→completed/failed with meta)
- [x] **Docker gate:** generated all 5 formats for a completed run in Compose; downloads work through nginx (magic bytes verified: `%PDF-`, `PK`; HTML contained 2 embedded failure screenshots; PDF 5 pages with image XObjects)

## Phase 13 — History & run comparison (§21)

**Goal:** Regression intelligence across executions.

- [ ] Execution history with trends; compare Run #N vs Run #M
- [ ] Diffs: new failures, resolved failures, persistent failures, new/removed tests, performance changes
- [ ] Comparison view in UI; **Docker gate:** two runs of `demo-app` (one with an injected defect) produce a meaningful diff

## Phase 14 — AI assistant & test data management (§22, §17) ✅

**Goal:** Grounded QA copilot + safe test data handling.

- [x] Assistant answering from actual project data (tool-calling/query over project tables — no hallucination): failure whys, critical lists, module defects, regression generation, run comparison, client summaries, API latency — chat panel on every project (`assistant` tab), intent-routed queries, LLM only rephrases verified data
- [x] LLM layer: OpenRouter support with UI-managed key (Settings → AI, Fernet-encrypted at rest, DB overrides .env), **auto-detection of the best free model** from the live /models catalog (modality-aware, family-ranked) and a rotation chain that skips rate-limited/retired/harness-only models (learned failure cache)
- [x] Test data management: environments (named targets, per-env variables), datasets (static with Fernet-encrypted secrets, generated via deterministic seeded generators), resolution preview (masked), executors substitute `{{placeholders}}`; env-scoped datasets only apply to their environment; secrets decrypted only inside the execution path — never in logs/API/reports
- [x] Also: password change, notification center + per-event settings, header bell with unread badge, worker-emitted notifications (run completion, report ready, security scan)
- [x] **Docker gate:** assistant questions about the seeded demo project returned correct, data-grounded answers (80 executions / 68.8% pass rate from real data); secret masking verified end-to-end; live OpenRouter completion with the user's own key; 14+2 execution run dispatched with `environment_id`

## Phase 15 — Production hardening & CI/CD (§29) ← next

**Goal:** Production-ready delivery on Docker Compose.

- [ ] `docker-compose.prod.yml`: pre-built immutable image tags, no source mounts, restart policies, resource limits (mem/cpu), non-root + read-only root filesystem where practical
- [ ] TLS termination at nginx (self-signed dev certs / certbot-ready), security headers, gzip
- [ ] `/scripts` + CI pipeline per §29: checkout → deps → lint → unit → integration → security scan → Docker build → container vuln scan → push (immutable tags) → staging deploy → smoke → prod approval → prod deploy
- [ ] Backup/restore runbook for Postgres + MinIO volumes
- [ ] **Docker gate:** CI pipeline green end-to-end; prod compose stack deploys and passes smoke tests

## Platform self-testing (§30 — continuous from Phase 1)

- Unit, API, frontend, integration, E2E, worker, database, and security tests in `/tests`
- `demo-app` (deliberately flawed) ships from Phase 2 as the default target so every phase is verifiable in Docker
- **Rule (§32):** after each phase — run tests, fix errors, validate APIs, validate migrations, validate frontend/backend integration, update docs. Never proceed broken.

---

## Acceptance criteria traceability (§34 → phase)

| # | Criterion | Phase |
|---|---|---|
| 1–4 | Open AutoQA, create project, enter URL, confirm authorization | 1, 2 |
| 5–6 | Start discovery, view discovered pages | 2 |
| 7–8 | View detected APIs / frontend technology | 3 |
| 9–10 | Generate test plan / test cases | 4, 5 |
| 11 | Review and approve test cases | 5 |
| 12–14 | Start execution, live progress, live logs | 6, 7 |
| 15–17 | Failure screenshots, analysis, retry | 8 |
| 18–20 | CSV / Excel / PDF reports | 12 |
| 21–22 | Execution history, compare runs | 13 |

**MVP milestone = end of Phase 12** (all 22 criteria). **Phases 0–12 complete.** Phases 13–15 are production hardening.
