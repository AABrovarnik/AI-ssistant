# Decisions

## 2026-08-14 — Утверждён формат dashboard

Context:

- Текущий проект — FastAPI без отдельного frontend toolchain.
- Продукт single-user, а dashboard нужен как owner-only read-only слой для
  наблюдения Gmail funnel.
- Основные требования — drill-down, безопасные метрики и простой deploy на
  существующем VPS.

Decision:

- Реализовать dashboard внутри текущего FastAPI-сервиса.
- Для MVP использовать серверный HTML через Jinja2, HTMX и небольшой JavaScript,
  без отдельного React/Vite-сервиса.
- Агрегаты и drill-down строить через owner-scoped JSON API поверх PostgreSQL.
- Сначала добавить durable `integration_poll_runs` и `task_candidates`, затем
  API и UI.
- Dashboard остаётся read-only; ручной Gmail poll не вызывается автоматически.

Alternatives rejected:

- Grafana не заменяет продуктовый Gmail funnel и drill-down до письма/candidate/
  задачи.
- Отдельный React-сервис преждевременно увеличит deploy и dependency surface для
  single-user MVP.
- Расчёт метрик в браузере создаёт риск расхождения aggregate и drill-down.

Consequences:

- Первый UI deploy не требует Node toolchain и отдельного контейнера.
- API-контракт можно позднее использовать для React или другого клиента.
- Для точных latency/freshness метрик потребуется сохранить историю polling и
  candidate lifecycle.

## 2026-08-14 — Dashboard строится вокруг достоверного Gmail funnel

Context:

- Пользователю нужен ответ не только на вопрос «жив ли агент», но и на вопрос,
  что произошло с письмами: обнаружено, обработано, отфильтровано, превращено в
  candidate и подтверждено как задача.
- В текущей схеме уже есть `source_messages`, `task_sources` и `task_events`, но
  история polling хранится только в `IntegrationAccount.last_polled_at`, а candidate
  lifecycle частично лежит в JSON metadata.

Decision:

- Делать owner-only read-only dashboard API-first, с web UI поверх API.
- Первым экраном считать KPI, funnel, freshness/health и drill-down, а не набор
  разрозненных графиков.
- Каждый KPI, funnel stage, bucket графика и health indicator должен иметь drill-down
  до конкретных исходных записей с теми же фильтрами и ссылками письмо → candidate → задача.
- Для production-достоверных метрик добавить durable `integration_poll_runs` и
  нормализованный `task_candidates`; заполнить `processed_at`, notification и
  decision timestamps.
- Считать письма по `received_at`, задачи по времени создания связи/задачи,
  polling по времени запуска. Не выдавать «все письма Gmail», если доступны только
  письма, обнаруженные polling.
- Не помещать постоянный internal bearer token в browser frontend; доступ должен
  быть owner-scoped через authenticated session или reverse-proxy auth.

Alternatives rejected:

- Строить dashboard только на Grafana: инфраструктурные time-series не дают
  безопасного user-facing funnel и drill-down по письмам.
- Считать notification delivery и latency по косвенным полям: это создаёт ложную
  точность и не показывает пропущенные polling runs.
- Сразу делать multi-user workspace: это расширяет scope до permission model,
  который ещё не является частью текущего single-user среза.

Consequences:

- Сначала появляется небольшой слой наблюдаемости и контракт метрик, потом API и UI.
- До добавления poll/candidate history dashboard должен маркировать часть данных как
  `partial` и явно показывать freshness.
- Подробное предложение сохранено в `docs/proposals/dashboard-proposal.md`; после
  утверждения ТЗ находится в `docs/dashboard-spec.md`.

## 2026-08-14 — Gmail polling has a protected manual trigger

Context:

- The Gmail worker can be unavailable or blocked by its per-account polling interval.
- A production incident showed that an empty `OPENCLAW_API_KEY` caused LLM requests to
  fail with HTTP 401 and left the Gmail account in `ERROR`, stopping future polls.

Decision:

- Add `POST /integrations/gmail/poll`, protected by the existing internal bearer token.
- Expose the same operation to the owner in Telegram as `/gmail_check` with `/gmail_poll`
  as an alias; report the candidate count and never create tasks automatically.
- The trigger bypasses the normal per-account cooldown, sends only candidate previews to
  the owner in Telegram, and never creates tasks or sends email automatically.
- Keep `OPENCLAW_API_KEY` aligned with the OpenClaw gateway auth token in deployment
  configuration, and restore a Gmail account to `CONNECTED` only after the dependency is
  healthy.

Alternatives rejected:

- Sending email replies automatically: outside the Gmail read-only safety boundary.
- Resetting `last_polled_at` manually in the database: not an observable or repeatable
  operator workflow.

Consequences:

- Operators can validate Gmail → LLM → Telegram without waiting 15 minutes.
- The endpoint must remain internal and rate-limited operationally; it is not a public
  user-facing API.

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
