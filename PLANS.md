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
- Full suite: `55 passed, 6 skipped`; `git diff --check` passed.
- VPS `147.45.238.131` (`ams-1-vm-cmsa`) verified read-only: Ubuntu 24.04, Docker enabled/active, all services running with `unless-stopped`, health endpoints OK, UFW default-deny, API bound to localhost, and PostgreSQL not publicly exposed.
- Production `.env` is mode `600` and untracked. The PostgreSQL backup systemd timer is enabled and its first run succeeded. A real PostgreSQL restart was not exercised to avoid widening the interruption; API and worker restart behavior was verified.
- Installed `ai-secretary-backup.service` and `ai-secretary-backup.timer`; the first run succeeded and its dump restored into an isolated temporary database with matching control counts. API and worker restart smoke test passed; PostgreSQL was intentionally not restarted.
- Remaining resilience caveat: backups are local-only; no offsite copy or backup alerting is configured.
