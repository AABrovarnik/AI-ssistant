# Current state handoff

Дата обновления: `2026-08-15`

## Цель

- Первый production-ready срез Telegram-first AI Secretary завершён.
- Интеграции Gmail и Google Calendar работают в opt-in режиме и сохраняют
  границу явного подтверждения пользователем.

## Статус

- Phase 1–9 завершены, включая E2E-hardening для задач, делегаций, Gmail,
  reminders и `AWAITING` follow-up.
- Phase 10 по безопасности и эксплуатации завершена.
- Локальный SQLite-набор тестов: `59 passed`; 6 PostgreSQL-тестов остаются opt-in skips.
- Dashboard Phase 12 реализован локально: approved ТЗ, migration `20260814_06`,
  durable poll runs/candidates, API и первый Jinja2/HTMX UI. Production migration
  and browser deployment smoke are still pending.
- Reviewed development pack хранится в
  `Posts/ai-secretary-v1.0-dev-pack/reviewed/`.

## Основные возможности

- PostgreSQL используется как источник истины; значимые изменения аудируются
  и выполняются идемпотентно.
- Telegram поддерживает создание, редактирование, завершение, перенос,
  отмену, поиск, обзоры, напоминания, делегации и ожидание результата.
- Свободный текст проходит через структурированный LLM parser и показывается
  как candidate preview; задача создаётся только после подтверждения.
- Классификатор считает «предоставить» и аналогичные формулировки запросом на
  выполнение (`TASK`/`DELEGATION`), а `INFORMATION` оставляет для запроса
  сведений без требования подготовить, найти, отправить или предоставить
  результат.
- Для `DELEGATED/AWAITING` без распознанного исполнителя используется статус
  `UNKNOWN_PARTY`; исполнитель назначается отдельным подтверждённым действием.
- Утренние, вечерние и недельные обзоры формируются только из PostgreSQL и не
  изменяют задачи.
- Reminder Engine детерминирован, дедуплицирован, учитывает quiet hours,
  retry/backoff и поддерживает snooze.
- Для просроченных `AWAITING` задач через сутки создаётся один
  `AWAITING_FOLLOW_UP`. Он отправляется только владельцу и предлагает
  `Результат получен` или `Напомнить завтра`; внешним контактам сообщения не
  отправляются.

## Gmail

- Gmail включается через `GMAIL_ENABLED=true` и использует только OAuth scope
  `gmail.readonly`.
- Access/refresh tokens хранятся зашифрованными через Fernet.
- Письма сохраняются по уникальному Gmail `message_id`, фильтруются и
  превращаются в candidate preview.
- Письмо не создаёт задачу автоматически; требуется подтверждение в Telegram.
- Начальная граница чтения задаётся `GMAIL_START_AT` и по умолчанию равна
  `2026-08-01T00:00:00+00:00`.

## Google Calendar

- Calendar включается через `GOOGLE_CALENDAR_ENABLED=true`.
- OAuth credentials хранятся зашифрованными; используется scope
  `calendar.events`.
- Создание события выполняется явно через
  `POST /tasks/{task_id}/calendar` после подключения и подтверждения.
- При переносе задачи или изменении срока связанное событие обновляется.
- Автоматическое создание событий, watch/webhook и recurring events не входят
  в текущий срез.

## Инфраструктура и безопасность

- Локальный Compose проверен; production PostgreSQL healthy, Alembic находится
  на `20260814_06 (head)` после деплоя dashboard.
- `/health/live` и `/health/ready` возвращают `{"status":"ok"}`.
- PostgreSQL backup выполняется systemd timer ежедневно в `03:15 UTC` с
  `Persistent=true` и задержкой до 10 минут.
- Локальные dumps имеют режим `600`, каталог backups — `700`; retention по
  умолчанию составляет 14 дней.
- Backup восстановлен в изолированную временную базу; live database не
  изменялась.
- Runtime log review не выявил Telegram URLs, Google OAuth URLs, bot tokens,
  OAuth secrets или passwords.
- VPS `147.45.238.131` проверен read-only: Docker и сервисы работают, UFW
  использует default-deny, API доступен только через localhost, PostgreSQL не
  опубликован наружу.
- API и worker прошли controlled restart smoke test. PostgreSQL намеренно не
  перезапускался, чтобы не расширять production interruption.
- Dashboard production smoke test пройден: unauthenticated API возвращает `401`,
  authenticated overview API и web dashboard возвращают `200`. API доступен на
  VPS через `127.0.0.1:8000`; для браузера нужен SSH tunnel или reverse proxy.

## Документация и runbooks

- Общий setup и обзор возможностей: [`README.md`](../../README.md).
- API, архитектура и схема БД: `Posts/ai-secretary-v1.0-dev-pack/reviewed/`.
- Runbook для `AWAITING` follow-up:
  [`docs/awaiting-follow-up.md`](../awaiting-follow-up.md).
- Runbook переноса на другой сервер:
  [`docs/migration-runbook.md`](../migration-runbook.md).
- Draft концепции следующего product/research этапа:
  [`docs/product-concept.md`](../product-concept.md).
- Backup units и скрипт: `ops/ai-secretary-backup.*`.
- Решения и ограничения: [`DECISIONS.md`](../../DECISIONS.md).
- Утверждённое ТЗ dashboard: [`docs/dashboard-spec.md`](../dashboard-spec.md).

## Оставшиеся ограничения

- Backups пока хранятся только локально на VPS.
- Offsite encrypted replication не настроена.
- Backup failure alerting не настроено.
- Отдельная предыдущая ошибка foreign-key для invalid assignee contact
  присутствовала в 24-часовом окне логов; при повторении её нужно расследовать.

## Следующие шаги

1. Выбрать offsite storage, схему шифрования и retention policy.
2. Добавить alerting при failed backup или пропущенном timer run.
3. При необходимости оформить отдельный production deployment runbook с
   матрицей `.env`, процедурой миграций, rollback и disaster recovery.
