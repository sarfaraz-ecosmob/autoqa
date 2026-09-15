---
name: git-init
description: >
  Use at the start of ANY new project or when a project has no git repository.
  Initializes git with a protective .gitignore, creates a baseline commit so
  every later change is revertible, and optionally sets up a feature-branch
  workflow. Triggers on: initialize git, start new project, set up backup,
  version control setup, we need to be able to revert code.
---

# Git Initialization Skill

Always run this **before writing any code** in a new project. The goal: every
change made afterward is committed, so the team can always revert.

## Steps

1. **Check state**
   ```bash
   git rev-parse --is-inside-work-tree 2>/dev/null || echo "NO_REPO"
   ls -a          # check for existing .gitignore / README
   ```

2. **If no repo exists, initialize**
   ```bash
   git init
   ```
   Use `git init -b main` when the git version supports it; otherwise
   `git init` then `git branch -m main`.

3. **Ensure `.gitignore` exists.** If missing, create one appropriate to the
   stack. Always include, regardless of stack:
   - `.env` and `.env.*` (but keep `!.env.example`) — never commit secrets
   - secret/key/cert patterns (`*.pem`, `*.key`, `secrets/`)
   - dependency dirs (`node_modules/`, `.venv/`, `venv/`)
   - cache/build dirs (`__pycache__/`, `dist/`, `build/`, `.pytest_cache/`)
   - OS/IDE noise (`.DS_Store`, `.idea/`, `.vscode/`)
   - local data/volumes (databases, uploads, generated artifacts)

4. **Baseline commit BEFORE building features.** This is the revert anchor:
   ```bash
   git add -A
   git commit -m "chore: initialize repository with baseline files"
   ```
   If the environment has no git identity configured, pass one per-command
   (do NOT edit global git config):
   ```bash
   env GIT_AUTHOR_NAME="Dev" GIT_AUTHOR_EMAIL="dev@localhost" \
       GIT_COMMITTER_NAME="Dev" GIT_COMMITTER_EMAIL="dev@localhost" \
       git commit -m "..."
   ```

5. **Commit after every completed unit of work** (phase, feature, bugfix) so
   each is independently revertible:
   ```bash
   git add -A
   git commit -m "feat(phase-N): <what was built>"
   ```
   Message style: `feat:`, `fix:`, `chore:`, `test:`, `docs:`, `refactor:`
   prefixes; imperative mood; one logical change per commit.

6. **Optional: feature branches** for risky work, merging to main after tests
   pass:
   ```bash
   git checkout -b feature/<topic>
   # ... work, commit ...
   git checkout main && git merge --no-ff feature/<topic>
   ```

7. **Remotes** — only configure a remote if the user provides one. Never push
   without explicit user permission.

## Rules

- NEVER commit `.env`, credentials, API keys, tokens, or large data dumps.
- NEVER amend or rewrite history on shared/main branches.
- Run the project's tests before each commit when practical; never commit
  known-broken code unless marking it clearly with `git checkout -b wip/...`.
- If a repository already exists, skip init — just verify `.gitignore` covers
  secrets and make sure the current work is committed before continuing.
