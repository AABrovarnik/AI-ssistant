# Execution Plan

## Objective

Implement the first Google Calendar integration slice after the Gmail MVP: explicit, confirmed task-to-event creation and event updates when a task deadline changes, with encrypted OAuth credentials and no automatic calendar writes by default.

## Non-goals

- Do not modify already stored Gmail source messages.
- Do not create calendar events without explicit confirmation.
- Do not implement Calendar watch/webhook, recurring events, or broad automatic sync in this step.

## Steps

- [completed] Define the Calendar API, OAuth scopes, persistence/migration, and explicit confirmation flow.
- [completed] Implement Calendar client, OAuth connection, event create/update/delete, and task sync status.
- [completed] Add focused tests for event mapping, idempotent update, OAuth/token handling, and confirmation boundaries.
- [completed] Fix task response serialization after Calendar sync and add a regression test.
- [completed] Run the full suite, deploy after verification, and perform a live smoke check without creating an event automatically.

## Phase 9 — E2E hardening progress

- [completed] Add delegation candidate confirmation E2E coverage: no task before confirmation, linked source after confirmation, and audit event.
- [completed] Define and implement the AWAITING follow-up suggestion flow; current code has reminders but no follow-up suggestion action.
- [completed] Add AWAITING candidate-to-follow-up E2E coverage after that flow exists.
- [completed] Add/verify the complete Gmail candidate confirmation path without external sending.
- [completed] Run the final E2E hardening suite and update the acceptance report.

The remaining project work is the separate security/operations and production checklist: backup/restore, health checks, sensitive-log review, and final VPS verification.

## Phase 10 — Security and operations checklist

- [completed] Verify local compose configuration, service health, migrations, and authenticated API health endpoints.
- [completed] Exercise PostgreSQL backup and restore into an isolated temporary database without touching live data.
- [completed] Review runtime logs for tokens, OAuth secrets, passwords, and full Telegram URLs.
- [completed] Verify production/VPS service status, firewall exposure, backups, and restart behavior.
- [completed] Run final diff/test checks and record any remaining deployment blockers.

## Assumptions

- “1 August” means 2026-08-01 in UTC for the current deployment.
- Calendar will reuse the existing encrypted integration-account storage, but will use a separate Google Calendar scope and provider value.
- Event creation will be exposed as an explicit task action/API call; `auto_calendar` remains opt-in and is not activated by this slice.

## Verification and rollback

- Verification: focused Calendar tests, then `.venv/bin/pytest -q` and `git diff --check` before deployment.
- Inspect the final diff and migration before deployment.
- Rollback: stop using Calendar endpoints and revert only the Calendar migration/code; existing Gmail records and task data remain untouched.

## Phase 10 verification and rollback

- Verification: `docker compose config`, `docker compose ps`, health endpoints, `alembic current`, isolated `pg_dump`/restore, targeted log scans, and `pytest -q`.
- Do not restore over the active PostgreSQL volume; use a temporary database or disposable container for restore validation.
- Rollback: remove only temporary backup/restore artifacts and stop any temporary verification container; do not alter the active database volume.

## Phase 10 verification result — 2026-08-14

- Local Compose services are up; PostgreSQL is healthy and Alembic is at `20260812_05 (head)`.
- `/health/live` and `/health/ready` returned `{"status":"ok"}` from the API container and host port.
- Backup restored successfully into `ai_secretary_restore_check`; migration, table count, and key row counts matched the live database. The temporary database was removed; the live database was left intact.
- Runtime log scan found no Telegram URLs, Google OAuth URLs, or bot-token-shaped values. A prior foreign-key error for an invalid assignee contact remains in the 24-hour log window and should be investigated separately if it recurs.
- Local suite: `57 passed, 6 PostgreSQL-opt-in skips`; `git diff --check` passed.
- VPS `147.45.238.131` (`ams-1-vm-cmsa`) verified read-only: Ubuntu 24.04, Docker enabled/active, all services running with `unless-stopped`, health endpoints OK, UFW default-deny, API bound to localhost, and PostgreSQL not publicly exposed.
- Production `.env` is mode `600` and untracked. The PostgreSQL backup systemd timer is enabled and its first run succeeded. A real PostgreSQL restart was not exercised to avoid widening the interruption; API and worker restart behavior was verified.
- Installed `ai-secretary-backup.service` and `ai-secretary-backup.timer`; the first run succeeded and its dump restored into an isolated temporary database with matching control counts. API and worker restart smoke test passed; PostgreSQL was intentionally not restarted.
- Remaining resilience caveat: backups are local-only; no offsite copy or backup alerting is configured.

## Phase 11 verification result — 2026-08-14

- `agent-work-review` and `production-closure-review` passed `quick_validate.py` and were installed in `/root/.codex/skills` from the versioned `skills/` copies.
- `.githooks/pre-commit`, `.githooks/pre-push`, `ops/install-git-hooks.sh`, and `ops/post-deploy-smoke.sh` passed `bash -n`.
- The full pre-push hook passed before the Telegram command change: `56 passed, 6 skipped`; the current full suite is `57 passed, 6 skipped`.
- The post-deploy smoke script returned healthy API checks and Gmail `CONNECTED` with a recent `last_polled_at`; it printed no credentials.
- A Calendar test resource leak discovered by hook verification was fixed by closing its async HTTP client.

## Phase 11 — Agent activity review and operational guardrails

Objective:

- Make agent self-review and runtime-closure checks repeatable for future multi-step work.

Steps:

- [completed] Add `agent-work-review` skill for independent evidence-based review of agent activity.
- [completed] Add `production-closure-review` skill for runtime/configuration/deployment closure.
- [completed] Add versioned Git hooks for staged secret checks, formatting/tests, and pre-push verification.
- [completed] Add a safe post-deploy smoke script for health and integration status without printing secrets.
- [completed] Add the owner-only Telegram `/gmail_check` command and `/gmail_poll` alias.
- [completed] Validate skills, hooks, scripts, and update project memory with known limitations.

Non-goals:

- Do not make hooks send Telegram messages or mutate production data automatically.
- Do not treat a self-review as access to hidden model reasoning; review only observable artifacts.

Verification:

- Validate each skill with `quick_validate.py`.
- Run hook scripts in a temporary copy or with controlled staged changes.
- Run the project test/lint commands and `git diff --check`.

Rollback:

- Remove the new project-local skills/hooks and restore the previous `PLANS.md` section; no application data is changed by this phase.

## Phase 12 — Dashboard implementation

### Objective

Implement the approved owner-only read-only dashboard described in
[`docs/dashboard-spec.md`](docs/dashboard-spec.md) for the Gmail → LLM → Telegram
→ Task funnel.

### Scope

- Add durable `integration_poll_runs` and normalized `task_candidates` data.
- Record processing, notification and decision timestamps without exposing secrets.
- Add owner-scoped dashboard aggregates, timeseries and drill-down API endpoints.
- Add a lightweight FastAPI/Jinja2/HTMX web UI in the existing service.
- Preserve the read-only boundary; manual Gmail polling remains an explicit
  operator action outside the analytical API.

### Steps

- [completed] Move the approved specification into project documentation and
  record architecture decisions.
- [completed] Add migration/models/indexes for poll runs and candidates.
- [completed] Persist polling outcomes and candidate lifecycle transitions.
- [completed] Implement dashboard query services and owner-scoped API.
- [completed] Implement overview, funnel, health and drill-down UI.
- [completed] Add focused tests, run the full suite and verify migration/rollback.

### Local implementation result — 2026-08-14

- `59 passed, 6 PostgreSQL-opt-in skips`.
- Ruff, mypy and `git diff --check` passed.
- PostgreSQL offline migration generation for `20260814_06` passed.
- Applying the migration to the deployment PostgreSQL and performing the
  authenticated production smoke check remain deployment steps, not local code
  blockers.

### Production deployment result — 2026-08-14

- Built and recreated the `api` and `worker` services on VPS `147.45.238.131`.
- Alembic is at `20260814_06 (head)`; PostgreSQL remained healthy.
- `/health/live` and `/health/ready` returned `{"status":"ok"}`.
- Unauthenticated dashboard API returned `401`; authenticated overview API and
  web dashboard returned `200`.
- The API is bound to `127.0.0.1:8000` on the VPS; public browser access still
  requires the existing access path or an SSH tunnel/reverse proxy.

### Assumptions

- The existing deterministic `SYSTEM_USER_ID` remains the owner scope for the
  single-user product.
- The existing internal bearer authentication protects the first API slice;
  browser session/reverse-proxy auth is a follow-up before public exposure.
- Historical rows without lifecycle timestamps are reported as partial data.

### Verification and rollback

- Verification: focused dashboard tests, full pytest suite, `git diff --check`,
  migration upgrade/downgrade or isolated database check, and authenticated API
  smoke requests.
- Rollback: stop serving dashboard routes and downgrade only the dashboard
  migration; do not alter existing Gmail messages or tasks.
