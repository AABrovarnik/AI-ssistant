# Decisions

## 2026-08-14 — Daily PostgreSQL backups use a systemd timer

Context:

- The VPS had no PostgreSQL backup cron or systemd job.
- The deployment already stores PostgreSQL data in Docker and mounts `backups/` into the database container.
- The production requirement is a daily dump with configurable retention and a tested restore procedure.

Decision:

- Use `/usr/local/sbin/ai-secretary-backup` with PostgreSQL custom-format `pg_dump`.
- Run it through `ai-secretary-backup.timer` daily at 03:15 UTC with `Persistent=true` and a 10-minute randomized delay.
- Keep 14 days of managed dumps by default; store the dump directory with mode `700` and dumps with mode `600`.
- Validate backups by restoring to a temporary PostgreSQL database; never restore over `ai_secretary`.
- Keep the first implementation local to the VPS. Offsite replication is not part of this change and remains a resilience follow-up.

Alternatives rejected:

- Root crontab: less observable than a systemd timer and does not expose missed-run state as clearly.
- Restarting PostgreSQL during the smoke test: unnecessary for this change and would widen the production interruption.
- Automatic offsite upload: requires a storage provider, credentials, retention policy, and encryption decision that were not specified.

Consequences:

- The timer and last-run result are inspectable with `systemctl` and `journalctl`.
- Local disk loss still destroys both the live database and local backups; offsite backup remains required for stronger disaster recovery.

## 2026-08-13 — AWAITING follow-up is an internal Telegram suggestion

Context: the reminder engine already plans deterministic deadline and overdue reminders, but the E2E plan requires a follow-up suggestion for overdue `AWAITING` tasks.

Decision:

- Create one idempotent `AWAITING_FOLLOW_UP` policy reminder at `due_at + 1 day`.
- Deliver it only to the owner through Telegram; do not contact assignees or third parties automatically.
- Offer `Результат получен` to complete the task and `Напомнить завтра` to create the next internal follow-up reminder.
- Keep task mutations and completion auditable through the existing `TaskService` operations; keep reminder delivery retryable and deduplicated.

Alternatives rejected:

- Sending a message to the assignee automatically: this violates the v1 confirmation boundary.
- Treating the existing generic overdue reminder as a follow-up: it does not provide an explicit follow-up action or distinguish `AWAITING` work.

Consequences:

- The reminder engine and Telegram callback protocol gain a small, tested follow-up contract.
- A future outbound-contact feature can build on this suggestion without changing the current safety boundary.
