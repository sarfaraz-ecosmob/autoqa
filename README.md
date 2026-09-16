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

| URL | What |
|---|---|
| http://localhost:8080 | AutoQA UI (via nginx) |
| http://localhost:8080/api/health | API health |
| http://localhost:8080/api/worker-ping | Celery round-trip check |
| http://localhost:8080/docs | OpenAPI docs |
| http://localhost:9000 | demo-app (default test target) |

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

Every phase is committed (see `git log`). The `.agents/skills/git-init/SKILL.md` skill initializes git + protective gitignore for any new project.

## Reports (§19)

Generate **CSV, Excel (XLSX), PDF, HTML, JSON** reports per project or per run from the Reports tab (or `POST /api/projects/{id}/reports`). Generation runs as a background job on the `reports` queue; download when status is `completed`. Reports include executive summary, app/environment info, test plan, execution stats, per-case results, defects, security findings, performance and accessibility results, embedded failure screenshots (HTML/PDF), and evidence-grounded recommendations.

## Screenshot evidence (§12)

UI tests capture a screenshot on every failure, and optionally on pass (per-test-case `capture_on_pass`, set during review). Screenshots are written by the browser-worker to a **shared `artifacts` volume**, registered as Artifact rows, and served as base64 through the API:

- Failure evidence viewer: click any execution row on a run page (or open a defect's evidence bundle)
- These artifacts feed the report generators in Phase 12 (embedded screenshots per §19)

## Security notes

- No secrets committed: `.env` is gitignored; `.env.example` documents keys.
- Networks are split: `edge` / `backend-net` / `worker-net` (egress control lands in Phase 1).
