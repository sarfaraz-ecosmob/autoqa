# AutoQA — Autonomous QA Platform

AI-powered end-to-end QA automation: give it a URL, it discovers the app, generates a test plan and test cases, executes them with Playwright, shows real-time results, and produces professional reports. See **`instructions.md`** (spec) and **`PLAN.md`** (phase-wise build plan).

## Stack

React + TS + Vite + Tailwind · FastAPI · Celery workers · PostgreSQL · Redis · MinIO · Playwright · Nginx — all in Docker Compose.

## Quickstart

```bash
cp .env.example .env        # defaults are fine for dev
docker compose up -d --build
open http://localhost:8080
```

The first account registered becomes the **admin** (open http://localhost:8080, register, sign in). Database migrations run automatically on backend startup (`entrypoint.sh` → `alembic upgrade head`), so a fresh server needs **no manual migration step**.

| URL | What |
|---|---|
| http://localhost:8080 | AutoQA UI (via nginx) |
| http://localhost:8080/api/health | API health |
| http://localhost:8080/api/worker-ping | Celery round-trip check |
| http://localhost:8080/docs | OpenAPI docs |
| http://localhost:9000 | demo-app (default test target) |

## Running on another server

Yes — the stack is self-contained. Minimum steps:

1. Install Docker + Compose plugin on the server
2. Copy the project (or `git clone`) and `cp .env.example .env`
3. **Before first start, change these in `.env`:**
   - `AUTOQA_SECRET_KEY` — long random string. This key derives the Fernet key that encrypts **all secrets at rest** (LLM API key, project credentials, test-data secrets). Set it before first use and never change it afterwards — changing it makes previously stored secrets undecryptable
   - `POSTGRES_PASSWORD` / `MINIO_ROOT_PASSWORD` / `AUTOQA_MINIO_SECRET_KEY`
   - `AUTOQA_LLM_API_KEY` (optional bootstrap AI key — see below; the UI-managed key overrides it)
4. `docker compose up -d --build`

That's it: migrations apply automatically, the demo test target starts, and the first registered user becomes admin. Open `http://<server-ip>:8080`.

Notes for servers:

- The **LLM key configured via the UI is stored in the database**, so it survives restarts and moves with the Postgres volume — no need to put it in `.env` on a new server. `.env`'s `AUTOQA_LLM_API_KEY` is only the fallback for fresh installs.
- Data lives in named volumes (`pgdata`, `miniodata`, `artifacts`). Copy these volumes to migrate an existing installation (see **Backups** below).
- The frontend is built into the image (`tsc && vite build`), so no Node is needed on the server.
- Firewall note: only port `8080` (UI/API) and optionally `9000` (demo-app) need exposure; everything else is internal.

> **New here?** See **[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)** — a hands-on tour of all 12 project stages with real screenshots from the demo-app.

## Stage-by-stage guide (project tabs)

Every project walks the same pipeline — each tab below is one stage. Typical flow: **Overview → Requirements → Discovery → APIs → Test Plan → Test Cases → Test Data → Executions → History → Quality → Assistant → Reports → Automations**. Stages 1–8 are the core loop (URL → tested requirements); the rest add non-functional depth and continuous operation.

### 1. Overview — project setup & authorization

> 📷 *A screenshot-annotated version of this guide lives in [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md).*

**Use:** Configure *what* AutoQA tests and prove you own the target.

**How it works:**

- Create the project with a name + base URL, then confirm the **authorization checkbox** — a legal gate; no scan starts without it.
- The **Test credentials & sign-in** card accepts: username/password (+ login URL, form selectors, success assertion), a static API token, or a token endpoint (`POST /login` → JSON path of the token). Saved via `PATCH /api/projects/{id}`; values are Fernet-encrypted at rest, masked (`••••••••`) in every API response, and decrypted only inside the execution path.
- Everything downstream reads this config: the crawler signs in before scanning, browser cases establish a session before their steps, API cases auto-attach the auth header, and generated cases reference `{{username}}`/`{{password}}` placeholders — never plaintext.

### 2. Requirements — from documents to test cases (PLAN V2.1)

**Use:** Turn BRDs/user stories/Jira exports into linked, executable test cases and prove coverage.

**How it works:** paste text or upload `.md/.pdf/.docx/.xlsx/.csv/.json` → requirements are parsed (`REQ-xxx` ids honored, duplicates skipped) → one click generates a review-gated scenario set per requirement (main flow, negative, boundary, injection; email flows add expired-link/reuse cases) → the **traceability matrix** shows requirement → cases → latest result → defects with a coverage summary.

### 3. Discovery — crawl & sitemap

**Use:** Map the application — pages, components, forms — the raw material for everything else.

**How it works:**

- A Playwright crawler (in the browser-worker) renders the app like a real user — not link-scraping — and records a component inventory per page (forms, buttons, inputs, modals, tables, search, file upload…).
- Safety controls: same-origin only, max depth / max URLs, dedupe, timeouts.
- If a login URL is configured, the crawler **signs in first**, so pages behind a portal are discovered too; login success is verified with the configured assertion (`url_not_contains:login`, `text_present:Welcome`, …).
- Progress streams live per scan; the result is a stored sitemap tree with per-page component counts.

### 4. APIs — backend & architecture discovery

**Use:** Inventory every backend endpoint the app calls, plus the detected frontend stack.

**How it works:**

- Network interception during the crawl records method/URL/headers/params/response for each API call; an OpenAPI/Swagger import adds anything not observed.
- Endpoints are **auto-grouped by function** (Auth, User, Product, Order, Payment, Reporting) — so checkout/payment surfaces are easy to spot (they trigger payment-scenario generation later).
- Frontend fingerprinting reports only technologies with real evidence (React, Vue, Next.js, WordPress…).
- Click **Analyze** to run this stage; the inventory feeds test planning and API test generation.

### 5. Test Plan — AI-generated coverage plan

**Use:** A structured, prioritized plan (objective, scope, categories) before any cases are written.

**How it works:**

- Discovery context (sitemap + API inventory + architecture) is fed to the LLM, which drafts a plan across all categories with priorities (Critical → Low). With no LLM key configured, a deterministic heuristic plan is generated instead — the stage always produces output.
- The plan is versioned and editable; **approve** it to lock scope before case generation.

### 6. Test Cases — generation & review gate

**Use:** Turn the plan into concrete, reviewable, executable cases — nothing runs without your approval.

**How it works:**

- One click generates cases with ref IDs (`TC-LOGIN-001`…), module, category, priority, preconditions, steps and expected results — positive **and** negative scenarios.
- Credentials never appear in stored cases: login steps use `{{username}}`/`{{password}}` placeholders resolved at run time.
- If discovery saw checkout/payment endpoints, payment scenarios are added: successful sandbox payment, **declined card** (`4000000000000002`, asserting a handled 4xx — never 5xx) and **idempotent replay** (same idempotency key must not double-charge). Always use your gateway's sandbox cards.
- Review actions: approve / reject / edit / disable / approve-all / approve-selected. **Only approved cases execute.**

### 7. Test Data — environments & datasets

**Use:** Per-environment data (staging vs UAT), secret datasets and generated data — resolved into `{{placeholders}}` at run time.

**How it works:**

- **Environments:** named targets (base URL + variables); runs accept an `environment_id`.
- **Datasets:** static (secret keys Fernet-encrypted at rest, masked everywhere) or **generated** (deterministic seeded users/emails/uuids/ints).
- Resolution order: project credentials < environment variables < active datasets — so Test Data can **override** project credentials per environment. Env-scoped datasets only apply to their environment. A resolution preview shows masked values.
- Secrets are decrypted only inside the execution path — never in logs, API responses or reports.

### 8. Executions — run control & live dashboard

**Use:** Run approved cases (browser + API) and watch results live — no refresh needed.

**How it works:**

- `POST /test-runs` (browser selection, retries, environment, parallelism) → start/pause/resume/stop; runs dispatch to Celery workers with a per-run parallelism cap that tops up as tests finish.
- **Browser cases:** each execution first establishes the authenticated session (the project's login flow), then runs its steps; a login failure reports `auth_login_failed` instead of a misleading "element not found". Failed selectors self-heal (fallback chain → grounded LLM proposal from the live DOM), recorded in evidence.
- **API cases:** the `Authorization: Bearer …` header is auto-attached from the token endpoint or static token; validations cover status, body, JSON schema, headers and response time.
- Live WebSocket counters (Total/Running/Passed/Failed/…), timestamped step logs, per-step timing, and screenshots/traces on failure stored as artifacts.

### 9. History — trends & run comparison

**Use:** Regression intelligence — what changed between runs.

**How it works:**

- Past runs with pass/fail/skip counts; pick any two runs as base + target and compare.
- The diff shows: new failures, resolved failures, persistent failures, new/removed tests, duration changes and performance (p95) shifts — e.g. "TC-CHECKOUT-002 failed in run #12 but passed in #11".

### 10. Quality — accessibility, performance & security

**Use:** Non-functional depth beyond functional pass/fail.

**How it works:**

- **Accessibility audit:** browser-based audit (labels, alt text, headings, lang, zoom) → violations per page with severity, fed into reports.
- **Performance:** user-defined VUs/RPS/duration → p50/p90/p95/p99, throughput, error rate; threshold breaches are flagged; guardrails (hard request caps) protect production targets.
- **Security scan** in three tiers (Passive → Safe Active → Authorized Full, the last requiring explicit re-confirmation), isolated worker, non-destructive payloads; findings carry severity and feed reports.

### 11. Assistant — grounded AI copilot

**Use:** Ask about your project in plain language: "why did tests fail?", "compare the last two runs", "give me a client summary".

**How it works:**

- Intent routing runs **real database queries first**; the answer is composed from actual project data — never invented.
- If an LLM key is configured (Settings → AI), it may only **rephrase** the verified answer, under a hard ~30s budget with silent fallback — so gateway timeouts can't recur. Works fine with no LLM at all.
- Answers are marked as grounded; the assistant cannot fabricate numbers.

### 12. Reports — professional deliverables

**Use:** Share results with stakeholders: **CSV, Excel, PDF, HTML, JSON** per project or per run.

**How it works:**

- Generation is a background job on the `reports` queue; download when the status is `completed`.
- Content: executive summary, app/environment info, test plan, execution stats, per-case results, defects, security/a11y/perf results, embedded failure screenshots (HTML/PDF) and evidence-grounded recommendations.

### 13. Automations — continuous testing

**Use:** Keep QA running without you: schedules, integrations, stability and visual monitoring.

**How it works:**

- **Schedules:** cron-driven regressions (UTC, croniter-validated) with browser selection, environment, parallelism and one-click "Run now"; Celery beat dispatches due schedules every minute.
- **Webhooks:** HMAC-SHA256-signed (`X-AutoQA-Signature`) Slack-compatible notifications on run completion; URLs Fernet-encrypted at rest.
- **Flaky detection:** instability score from execution history (retried-pass, alternating outcomes, pass-rate drift) → stable / suspect / flaky classification.
- **Visual testing:** approved baselines per case × browser, pixel-diff with red-overlay diff artifact (0.5% threshold), failed check → review → approve-as-new-baseline loop.
- **Recorder import:** paste Playwright `codegen` output → becomes a reviewable test case; **self-healing locators** try fallback chains, then a grounded LLM proposal — heals are recorded in evidence, stored cases are never silently rewritten.

## Database migrations

Migrations are **automatic**: the backend image's entrypoint runs `alembic upgrade head` on every container start, so `docker compose up -d` on a fresh server produces a fully-migrated schema with zero manual steps. On existing databases the check is a cheap no-op.

For day-to-day development (adding a migration), run Alembic inside the backend container:

```bash
# Check current revision / full history (exec = no side effects)
docker compose exec backend alembic current
docker compose exec backend alembic history

# Apply pending migrations manually (rarely needed — startup does it)
docker compose run --rm backend alembic upgrade head

# Roll back one revision — NOTE: use --entrypoint to bypass the auto-upgrade,
# otherwise each `run` container migrates to head before running your command
docker compose run --rm --entrypoint alembic backend downgrade -1

# Create a new migration after editing app/models.py
#   --autogenerate diffs the models against the DB; ALWAYS review the
#   generated file in backend/migrations/versions/ before applying.
docker compose run --rm backend alembic revision --autogenerate -m "describe change"
```

> Because the entrypoint auto-migrates, `docker compose run --rm backend alembic ...` always runs against the latest head. That's desirable for `upgrade`/`revision`, but for `downgrade` (or inspecting a specific revision) bypass it with `--entrypoint alembic` as shown above.

Migration files live in `backend/migrations/versions/` (`0001` initial schema → `0005` test data). When you add a migration, rebuild so it ships in the image:

```bash
docker compose build backend worker security-worker browser-worker
docker compose up -d
```

> **Fresh volume, no tables?** If you ever wipe the `pgdata` volume, just restart the backend — the entrypoint recreates the full schema from `0001`.

> **Autogenerate on SQLite won't work** — always generate/apply migrations against the Postgres container as shown above.

## Changing the OpenRouter API key

Two ways:

- **UI (recommended):** Settings → **AI Assistant** → paste the new key → Save. Requires an admin account. The key is Fernet-encrypted at rest, never displayed again (only masked like `sk-••••217`), and takes effect immediately — "Test connection" verifies it and reports the auto-detected model.
- **API:** `PUT /api/settings/ai` with `{"provider": "openrouter", "model": "auto", "api_key": "sk-or-v1-..."}`. Sending an **empty** `api_key` clears the stored key (falls back to `.env`).

The key is stored in the DB (`llm_settings`, singleton) and overrides `.env`. Model: leave `auto` to auto-pick the best free OpenRouter model, or pin a slug like `z-ai/glm-5.2:free`.

## Developer commands

```bash
./scripts/dev.sh up         # build + start everything
./scripts/dev.sh ps         # service status
./scripts/dev.sh logs backend
./scripts/dev.sh test-be    # backend pytest in container
./scripts/dev.sh test-fe    # frontend vitest in container
./scripts/dev.sh down       # stop
./scripts/dev.sh clean      # stop + remove volumes
```

## Layout

```
/frontend   React SPA
/backend    FastAPI + Celery (one image, 3 roles: api/worker/scheduler)
/demo-app   deliberately flawed test target for AutoQA itself
/docker     nginx config
/tests      cross-service E2E (later phases)
/scripts    dev helper scripts
```

## Git & backups

Every phase is committed (see `git log`). The `.agents/skills/git-init/SKILL.md` skill initializes git + protective gitignore for any new project. For production data backups (Postgres dump + artifacts volume), see **Backups** at the end of this document.

## Reports (§19)

Generate **CSV, Excel (XLSX), PDF, HTML, JSON** reports per project or per run from the Reports tab (or `POST /api/projects/{id}/reports`). Generation runs as a background job on the `reports` queue; download when status is `completed`. Reports include executive summary, app/environment info, test plan, execution stats, per-case results, defects, security findings, performance and accessibility results, embedded failure screenshots (HTML/PDF), and evidence-grounded recommendations.

## Screenshot evidence (§12)

UI tests capture a screenshot on every failure, and optionally on pass (per-test-case `capture_on_pass`, set during review). Screenshots are written by the browser-worker to a **shared `artifacts` volume**, registered as Artifact rows, and served as base64 through the API:

- Failure evidence viewer: click any execution row on a run page (or open a defect's evidence bundle)
- These artifacts feed the report generators in Phase 12 (embedded screenshots per §19)

## Automations (Tier-1)

Project → **Automations** tab:

- **Scheduled runs** — cron-driven regressions (UTC, croniter-validated) with browser selection, environment, parallelism cap, and one-click "Run now". Celery beat dispatches due schedules every minute.
- **Flaky test detection** — per-case instability score (retried-pass, alternating outcomes, pass-rate drift) → stable / suspect / flaky.
- **Visual testing** — Percy-style baselines per test case × browser. Passing runs are pixel-diffed automatically (0.5% threshold, red-overlay diff artifact). Failed check → review → "Approve as baseline". Baseline upload via API accepts base64 PNG.
- **Webhooks** — HMAC-SHA256-signed (`X-AutoQA-Signature: sha256=<hmac>`, `X-AutoQA-Timestamp`) Slack-compatible notifications on run completion. URLs are Fernet-encrypted at rest and only ever shown masked; a signing secret is shown once at creation.
- **Test recorder import** — paste Playwright `codegen` output; supported actions (goto/fill/click, getByRole/getByTestId/getByLabel/getByText/getByPlaceholder) become a reviewable test case; unsupported lines are listed as warnings.
- **Self-healing locators** — when a selector fails, fallbacks are tried (data-testid ↔ data-test/data-cy variants → id → role/name → text → css class), then a grounded LLM proposal from the live DOM (must match exactly one element). Heals are recorded in the execution evidence — stored cases are never silently rewritten.

## Authenticated targets (credentials, portals & payment flows)

Many targets sit behind a login or require auth for their APIs. AutoQA handles this with **project credentials + an auth flow**, configured per project (**Project → Overview → Test credentials & sign-in**): a plain login form, a portal, or a token-authenticated SPA — the credentials are Fernet-encrypted at rest, shown masked (`••••••••`) in every API response, substituted at execution time only, and scrubbed from logs, evidence and reports.

**How it flows through the platform:**

1. **You provide credentials** on the project Overview card: username/password (and optionally a login URL + form selectors), or a static API token, or a token endpoint (`POST /api/auth/login` → JSON path of the token). Stored via `PATCH /api/projects/{id}` as `credentials` (values) + `auth` (flow config); legacy flat credentials still decrypt fine.
2. **Discovery scans sign in first** — with a login URL configured, the crawler performs the login flow before crawling, so pages behind the portal are discovered too. Login success is checked with a configurable assertion (e.g. `url_not_contains:login`, `text_present:Welcome`).
3. **Generated login tests use `{{placeholders}}`** — `{{username}}`/`{{password}}` resolve at run time from the encrypted credentials. No plaintext secrets in test cases, ever.
4. **Every test execution signs in before its steps run** (browser cases) — so protected pages, dashboards and multi-step portal flows execute inside an authenticated session. If the login fails, the case fails with `auth_login_failed` (not a misleading "element not found"). Selectors self-heal like normal steps.
5. **API cases get the auth header auto-attached** — `Authorization: Bearer <token>` from the token endpoint (or your static value) is merged into every request; step-level headers can override per case.
6. **Per-environment credentials** come from Test Data: variables/datasets (with per-environment scoping and encrypted secrets) merge over project credentials and can override them per environment (staging vs UAT).

**Payment gateways & portals:** when discovery sees checkout/payment endpoints, generation adds payment scenarios: successful sandbox payment, declined card (standard `4000000000000002` decline path, asserting a handled 4xx — never 5xx), and idempotent replay (same idempotency key must not double-charge). Card data is supplied via `{{placeholders}}` from a dataset — **always use your gateway's sandbox/test card numbers, never real card data**; AutoQA drives your app's test environment and asserts observable behavior (status codes, response bodies, no leaked internals), it never touches real money.

API for credential management (also used by the UI):

```bash
PATCH /api/projects/{id}
{
  "credentials": {"username": "qa@corp.com", "password": "..."},
  "auth": {
    "login_url": "https://staging.example.com/login",
    "username_selector": "input[name='email']",
    "password_selector": "input[type='password']",
    "submit_selector": "button[type='submit']",
    "success_assert": "url_not_contains:login",
    "api_auth_header": "Authorization",
    "api_auth_prefix": "Bearer ",
    "api_token_request": {"method": "POST", "path": "/api/auth/login",
                          "json": {"username": "{{username}}", "password": "{{password}}"},
                          "token_path": "token"}
  }
}
```

## Requirements & Traceability (PLAN V2.1)

Project → **Requirements** tab: paste requirement text (blank-line or heading separated; `REQ-xxx:` ids optional) or upload **.md / .txt / .pdf / .docx / .xlsx / .csv / .json (Jira export)**. Each requirement generates a review-gated scenario set — main flow, invalid input, missing data, boundary, injection (plus expired-link / link-reuse scenarios for email/reset requirements). API requirements (detected via `POST /path` style text) generate API cases; the rest generate browser cases.

- **Refs**: `REQ-AUTH-001` → `TC-AUTH-001-001…`; re-runs are idempotent (existing refs are skipped, never duplicated).
- **Review gate**: generated cases are created unapproved — approve them in Test Cases like any other case. Cases are linked via `RequirementLink` rows with coverage kind (`ui|api|negative|boundary|security`).
- **Traceability matrix**: requirement → cases → latest result per case (PASS/FAIL/blocked/never-run chips) → defect count, with a coverage summary (requirements / covered / uncovered / pass rate). This is the audit artifact enterprise QA teams need: "every requirement has tests, and here's their latest status."
- **LLM polish (optional)**: with `use_llm: true` and a configured key, scenario titles/data are refined under a hard 30s budget with silent fallback to the deterministic set — the LLM never invents new scenario kinds or credentials.

API:

```bash
POST /api/projects/{id}/requirements/import        {"text": "REQ-...", "source_name": "brd"}
POST /api/projects/{id}/requirements/import-file   multipart: file=@brd.md
POST /api/projects/{id}/requirements/generate-cases {"requirement_ids": [...], "use_llm": false}
GET  /api/projects/{id}/requirements               # + coverage_count per row
GET  /api/projects/{id}/requirements/{req_id}      # + linked cases
PATCH/DELETE /api/projects/{id}/requirements/{req_id}
GET  /api/projects/{id}/requirements/traceability  # matrix + summary
```

## AI Assistant & Test Data (§22, §17)

- **AI assistant** (project → Assistant tab): ask in natural language — "why did tests fail?", "which module has the most defects?", "compare the last two runs", "give me a client summary". Every answer is **grounded**: intent routing runs real DB queries and the LLM (if configured) may only rephrase verified data — never invent numbers. Works with `provider=none` (heuristic answers) too. The heuristic answer is composed **first** from real data; the LLM only rephrases it under a hard ~30s budget, so slow free models can no longer cause gateway timeouts (nginx proxy window for `/api/` raised to 120s as a safety net).
- **Test data** (project → Test Data tab): named **environments** (staging/uat with per-env variables), **datasets** — static (secret keys Fernet-encrypted at rest, masked `••••••••` in every API response and the resolution preview) or **generated** (deterministic seeded users/emails/uuids/ints). Runs accept an `environment_id`; active datasets merge over env variables and are substituted into `{{placeholders}}` by the API and browser executors. Env-scoped datasets only apply to their environment; secrets are decrypted only inside the execution path.
- **Model auto-detection**: with `model=auto`, the best **free** OpenRouter model is picked from the live catalog (chat-capable, modality-filtered, family-ranked, 1-hour cache). Rate-limited/retired models are skipped automatically via the rotation chain.

## Settings (§22, §31)

The **Settings** page (header → Settings, admin sections marked) provides:

- **Password change** — verifies the current password, bcrypt-hashed (§25)
- **AI Assistant** — add/replace the OpenRouter or OpenAI API key from the UI; the key is Fernet-encrypted at rest (§17), shown only masked (`sk-••••ef`), and takes precedence over `AUTOQA_LLM_API_KEY` in `.env` (which remains the bootstrap fallback). With model `auto`, the best **free** OpenRouter model is detected at runtime and cached for 1 hour. "Test connection" verifies the key and reports the resolved model.
- **Notifications** — per-event in-app notification settings (run completed / failed tests / scan / report / a11y / perf) plus an inbox with unread badge in the header bell. Workers emit real events; disabled event types are never stored.

## Security notes

- No secrets committed: `.env` is gitignored; `.env.example` documents keys.
- Secrets at rest (LLM key, project credentials, test-data secrets) are Fernet-encrypted; the key derives from `AUTOQA_SECRET_KEY`.
- Networks are split: `edge` / `backend-net` / `worker-net` (egress control lands in Phase 1).

## Backups

```bash
# Postgres (all projects, users, results, encrypted secrets)
docker compose exec postgres pg_dump -U autoqa autoqa > backup_$(date +%F).sql

# Restore
cat backup_2026-09-16.sql | docker compose exec -T postgres psql -U autoqa autoqa

# Artifacts volume (screenshots, reports) — named volume `artifacts`
docker run --rm -v autoqa_artifacts:/data -v $PWD:/backup alpine \
  tar czf /backup/artifacts_$(date +%F).tar.gz -C /data .
```

MinIO (`miniodata`) backs screenshots/traces that also live in `artifacts`; back up `pgdata` + `artifacts` at minimum. Schedule both via cron for production use (full automation is part of Phase 15).
