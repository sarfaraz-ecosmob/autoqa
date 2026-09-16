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

## AI Assistant & Test Data (§22, §17)

- **AI assistant** (project → Assistant tab): ask in natural language — "why did tests fail?", "which module has the most defects?", "compare the last two runs", "give me a client summary". Every answer is **grounded**: intent routing runs real DB queries and the LLM (if configured) may only rephrase verified data — never invent numbers. Works with `provider=none` (heuristic answers) too.
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
