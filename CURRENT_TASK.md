# Current Task

## Now
- Phase 10 operations checklist complete; local backup automation and controlled API/worker restart smoke test passed.
- Gmail polling restored on 2026-08-14 after configuring the OpenClaw API key, resetting the
  Gmail account from `ERROR` to `CONNECTED`, and verifying a successful manual poll.
- Added protected `POST /integrations/gmail/poll` for immediate operator-triggered checks.
- Added owner-only Telegram commands `/gmail_check` and `/gmail_poll` using the same forced-poll callback.
- Added and installed `agent-work-review` and `production-closure-review` skills, plus versioned Git and post-deploy verification hooks.
- Dashboard Phase 12 implementation is underway: approved specification moved to
  `docs/dashboard-spec.md`; observability schema, Gmail/candidate lifecycle,
  owner-scoped API and first web UI are implemented locally.

## Done
- Phase 9 E2E hardening; local suite currently passes 57 tests with 6 PostgreSQL-opt-in skips.
- Local Compose, health endpoints, migrations, isolated PostgreSQL backup/restore, and runtime secret-log review completed on 2026-08-14.
- Dashboard-focused tests and the existing suite currently pass `59 passed, 6 skipped`.

## Next
- Apply and verify migration `20260814_06` against the project PostgreSQL environment.
- Perform authenticated dashboard smoke checks and decide the browser reverse-proxy deployment path.
- Then consider offsite encrypted backup replication and backup failure alerting as a follow-up.

## Blockers
- Backups are local-only; offsite replication and alerting are not configured.
