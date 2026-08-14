# Migration runbook

## Назначение

Этот runbook описывает перенос AI Secretary на новый VPS с сохранением задач,
истории, reminder state и зашифрованных OAuth credentials.

Перенос предполагает короткое окно недоступности. Старый и новый worker нельзя
запускать одновременно: при `TELEGRAM_MODE=polling` они могут конкурировать за
получение обновлений Telegram.

## Что переносится

- код проекта на том же Git commit;
- PostgreSQL database через custom-format `pg_dump`;
- `.env` и секреты с правами `600`;
- `TOKEN_ENCRYPTION_KEY` — обязательно, иначе существующие Gmail/Calendar
  tokens нельзя будет расшифровать;
- Telegram token и owner ID;
- доступ к OpenClaw Gateway и новый `OPENCLAW_BASE_URL`/`OPENCLAW_API_KEY`;
- при необходимости — существующие backup dumps.

Docker named volume `postgres_data` не нужно копировать вручную. Надёжнее
перенести данные через проверенный PostgreSQL dump.

## До начала работ

На старом сервере проверьте:

```bash
cd /root/projects/AI-ssistant
docker compose ps
docker compose exec -T db sh -c \
  'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
```

Зафиксируйте:

- Git commit, который будет развернут на новом сервере;
- значения `GMAIL_REDIRECT_URI` и `GOOGLE_CALENDAR_REDIRECT_URI`;
- внешний адрес нового OpenClaw Gateway;
- способ доступа к новому VPS и правила firewall;
- место для временного dump и его контрольную сумму.

Если callback URL изменится, заранее добавьте новый URL в Google Cloud Console.
Старый callback можно оставить до завершения миграции.

## Подготовка нового сервера

Установите Docker Engine и Docker Compose plugin, создайте каталог проекта и
получите тот же commit:

```bash
mkdir -p /root/projects/AI-ssistant
cd /root/projects/AI-ssistant
git clone <REPOSITORY_URL> .
git checkout <COMMIT>
```

Скопируйте `.env` защищённым способом и проверьте права:

```bash
install -m 600 /path/to/migrated.env /root/projects/AI-ssistant/.env
```

На новом сервере измените только значения, зависящие от инфраструктуры:

- `OPENCLAW_BASE_URL` и `OPENCLAW_API_KEY`;
- `API_BIND_HOST`/`API_PORT`, если меняется reverse proxy;
- Gmail/Calendar redirect URI при смене домена;
- `TIMEZONE` только если это осознанное изменение поведения приложения.

Не генерируйте новый `TOKEN_ENCRYPTION_KEY` при переносе существующей базы.

## Предварительная проверка восстановления

До cutover можно проверить перенос на копии последнего dump. Скопируйте dump на
новый сервер и проверьте checksum:

```bash
sha256sum /path/to/ai_secretary_YYYYMMDDTHHMMSSZ.dump
```

Запустите только PostgreSQL:

```bash
cd /root/projects/AI-ssistant
docker compose up -d db
docker compose ps db
```

На чистой базе восстановите dump. Этот пример предназначен для нового,
пустого Compose volume; не используйте его поверх нужной рабочей базы:

```bash
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' \
  < /path/to/ai_secretary_YYYYMMDDTHHMMSSZ.dump
```

Проверьте миграцию и контрольные данные:

```bash
docker compose run --rm api alembic current
docker compose exec -T db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM tasks"'
```

После такой проверки можно удалить только временный target volume/инстанс,
если он был создан исключительно для rehearsal. Не удаляйте production data.

## Cutover с коротким простоем

### 1. Остановить запись на старом сервере

В согласованное окно остановите API и worker, оставив PostgreSQL запущенным:

```bash
cd /root/projects/AI-ssistant
docker compose stop api worker
```

Это предотвращает изменения задач во время финального dump. Не запускайте
старый worker снова, пока новый worker не остановлен.

### 2. Сделать финальный dump

```bash
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "/tmp/ai_secretary_${stamp}.dump"
chmod 600 "/tmp/ai_secretary_${stamp}.dump"
sha256sum "/tmp/ai_secretary_${stamp}.dump"
```

Перенесите dump на новый сервер по защищённому каналу и снова проверьте
checksum. Не передавайте dump через публичный файловый обмен без шифрования.

### 3. Восстановить финальный dump на новом сервере

На новом сервере убедитесь, что PostgreSQL запущен на чистом target:

```bash
cd /root/projects/AI-ssistant
docker compose up -d db
docker compose ps db
```

Восстановите dump до запуска API и worker:

```bash
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' \
  < /path/to/final/ai_secretary_YYYYMMDDTHHMMSSZ.dump
```

Если target database уже содержит данные, остановитесь и сначала сделайте
отдельный изолированный restore. Не используйте `--clean` без проверки точной
целевой базы.

### 4. Запустить приложение на новом сервере

```bash
docker compose up -d --build api worker
docker compose ps
```

API автоматически применит Alembic migrations перед запуском Uvicorn. Проверьте
health endpoints и текущую миграцию:

```bash
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
docker compose exec -T api alembic current
```

### 5. Проверить интеграции

Проверка должна включать:

- authenticated API request с `INTERNAL_API_TOKEN`;
- Telegram `/start` или `/help` от owner account;
- один безопасный worker/LLM smoke test;
- наличие старых задач, reminders и audit events;
- Gmail/Calendar status endpoints;
- отсутствие token-shaped values и полных provider URLs в runtime logs.

Не создавайте Calendar event и не отправляйте внешние сообщения только ради
проверки миграции. Для Gmail/Calendar достаточно проверить status/OAuth state;
при смене callback URL отдельный OAuth reconnect может потребоваться позже.

## Backup timer на новом сервере

Установите units после проверки приложения. Если проект находится не в
`/root/projects/AI-ssistant`, обновите `WorkingDirectory` в service и задайте
`AI_SECRETARY_PROJECT_DIR` для backup script:

```bash
install -m 0750 ops/ai-secretary-backup.sh /usr/local/sbin/ai-secretary-backup
install -m 0644 ops/ai-secretary-backup.service ops/ai-secretary-backup.timer \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ai-secretary-backup.timer
systemctl start ai-secretary-backup.service
systemctl status ai-secretary-backup.timer --no-pager
```

Проверьте, что новый dump создаётся в ожидаемом каталоге и имеет права `600`.
Не удаляйте старые backups до успешной проверки нового timer run и restore.

## Rollback

До первой записи на новом сервере rollback простой:

1. Остановить новый `api` и `worker`.
2. Проверить, что новый worker точно не работает.
3. Запустить старые `api` и `worker`.
4. Оставить новый сервер выключенным до выяснения причины.

После появления новых записей на новом сервере базы расходятся. В этом случае
нельзя просто запустить старый worker: сначала требуется сохранить новый dump,
сравнить изменения и выполнить согласованный reverse restore/reconciliation.

## После миграции

- Оставьте старый сервер доступным, но не запускайте его worker.
- Сохраните финальный dump и checksum до завершения контрольного периода.
- Перенесите backups в offsite encrypted storage, если он настроен.
- Обновите DNS/reverse proxy/firewall и Google OAuth callback allowlist.
- Зафиксируйте новый адрес сервера и результат проверки в operational notes.
- После подтверждения стабильной работы отзовите старые ключи и удалите старую
  инсталляцию по отдельному согласованному плану.
