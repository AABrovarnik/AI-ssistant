# Техническое задание: dashboard AI Secretary

Статус: `APPROVED`
Дата: `2026-08-14`
Связанный документ: [предложение по dashboard](proposals/dashboard-proposal.md)

## 1. Цель

Создать owner-only read-only web dashboard для наблюдения за цепочкой:

```text
Gmail → LLM → Telegram → подтверждённая задача
```

Dashboard должен показывать не только состояние сервисов, но и судьбу писем:
сколько сообщений система обнаружила, обработала, отфильтровала, превратила в
candidate и довела до подтверждённой задачи.

Каждый KPI, этап funnel, элемент графика и health indicator должен раскрываться
до конкретных исходных записей с сохранением применённых фильтров.

## 2. Формат реализации

Dashboard реализуется внутри текущего FastAPI-проекта, отдельный сервис не
создаётся.

### Backend

- FastAPI read-only endpoints для агрегатов и drill-down.
- SQLAlchemy query/service layer поверх PostgreSQL.
- Все агрегаты считаются на сервере, а не в браузере.
- Owner scope берётся из auth context; браузер не передаёт произвольный
  `user_id`.

### Web UI

- Серверная HTML-страница через Jinja2.
- HTMX и небольшой JavaScript для фильтров, side panel и обновления таблиц.
- Простая CSS-система без обязательного Node/Vite toolchain на первом этапе.
- Графики допускаются на базе лёгкого клиентского решения или SVG; график не
  должен быть единственным способом получить данные.

### Развёртывание

- Dashboard поставляется вместе с API в существующем контейнере.
- Отдельная база, frontend-сервис и Grafana для MVP не требуются.
- API-контракты должны оставаться независимыми от выбранного HTML-клиента,
  чтобы позднее можно было заменить UI на React без переписывания query layer.

## 3. Границы MVP

### Входит в MVP

- Обзор за выбранный период.
- KPI, funnel, timeseries и health/freshness.
- Списки писем, candidates, задач и polling runs.
- Drill-down для каждого агрегата.
- Owner-only authentication и read-only доступ.
- UTC-хранение времени и отображение в часовом поясе пользователя.
- Маркировка неполных исторических данных.

### Не входит в MVP

- Multi-user workspaces, роли и сложная ACL-модель.
- Grafana как пользовательский dashboard.
- Автоматическое создание задач по письмам.
- Автоматическая отправка ответов на Gmail.
- Изменение, удаление или подтверждение задач из dashboard.
- Gmail watch/webhook, recurring events и широкая автоматическая синхронизация.
- Полный текст письма в таблицах и агрегатных API.

Ручной Gmail poll может быть доступен как явно подтверждаемое операторское
действие через существующий защищённый endpoint, но не является частью
read-only аналитики.

## 4. Пользовательский сценарий

1. Владелец открывает `/dashboard` и выбирает период.
2. Dashboard показывает время формирования данных, freshness и ограничения
   качества данных.
3. Владелец нажимает KPI или этап funnel.
4. Открывается side panel либо отдельный список с теми же фильтрами.
5. Из строки письма можно перейти к Gmail message, candidate и связанной задаче.
6. Из задачи можно вернуться к исходному письму.
7. Из health-блока можно открыть историю polling runs и безопасные ошибки.

URL должен содержать воспроизводимое состояние фильтров, например:

```text
/dashboard/messages?from=2026-08-01&to=2026-08-14&status=FAILED&bucket=day
```

## 5. Фильтры и временные оси

Общие фильтры:

- `from`, `to` — диапазон дат;
- `timezone` — часовой пояс отображения;
- Gmail account/provider;
- processing status;
- classification/candidate status;
- task status и priority;
- источник данных и bucket графика.

Хранение и SQL-сравнение выполняются в UTC. На границах диапазона используется
полуинтервал `[from, to)`.

Нельзя смешивать разные временные оси:

| Метрика | Поле времени |
|---|---|
| Обнаруженные и обработанные письма | `source_messages.received_at` |
| Время обработки | `source_messages.processed_at` |
| Candidate lifecycle | `detected_at`, `notified_at`, `decided_at` |
| Созданные задачи из Gmail | `task_sources.created_at` или `tasks.created_at` |
| Polling | `integration_poll_runs.started_at` |

Каждый агрегат возвращает `count_basis`, `period` и `data_quality`, чтобы UI не
создавал ложное ощущение сопоставимости.

## 6. Главный экран

### 6.1 Верхняя панель

- название dashboard;
- период и timezone;
- Gmail account;
- `generated_at`;
- последний успешный poll;
- возраст данных;
- ссылка на operations;
- явная кнопка обновления данных.

Если polling старше допустимого интервала, показывается `STALE`, даже если
статус integration account равен `CONNECTED`.

### 6.2 KPI

MVP показывает следующие карточки:

| KPI | Определение |
|---|---|
| Писем обнаружено | `COUNT(DISTINCT source_messages.id)` для Gmail по `received_at` |
| Обработано успешно | письма со статусом `PROCESSED` |
| Отфильтровано | `IGNORED` с Gmail filter decision `IGNORE` |
| Ошибок обработки | письма со статусом `FAILED` |
| Candidate найдено | нормализованные candidates в выбранном периоде |
| Ожидают решения | candidates без финального решения и без задачи |
| Создано задач из Gmail | distinct task links с relation `CREATED_FROM` |
| Конверсия в задачи | созданные задачи / candidates |

Карточка содержит абсолютное значение, сравнение с предыдущим сопоставимым
периодом, определение и ссылку на drill-down.

Отфильтрованные письма не считаются ошибками LLM. Ошибки LLM и ошибки Telegram
delivery должны быть различимы.

### 6.3 Funnel

```text
Обнаружено → Сохранено → Обработано
                     ├→ Отфильтровано
                     ├→ Ошибка
                     └→ Candidate → Уведомлено в Telegram → Подтверждено как задача
```

Каждый этап кликабелен. Для него возвращаются `value`, `definition`,
`count_basis`, фильтры и стабильный drill-down query.

### 6.4 Графики

Минимальный набор:

- письма, candidates и задачи по дням;
- processed/filtered/failed по дням;
- candidates по типу `MY_TASK`, `DELEGATED`, `AWAITING`;
- задачи из Gmail по статусу и priority.

Среднее и p95 время от получения до обработки добавляются только после появления
достоверных `processed_at` и истории polling. Каждая точка или столбец является
фильтром для drill-down.

## 7. Operations и health

Health-блок показывает:

- Gmail status: `CONNECTED`, `ERROR`, `DISCONNECTED`;
- последний успешный и последний завершённый poll;
- ожидаемый интервал следующего poll;
- возраст данных и stale flag;
- число ошибок за период;
- последнюю безопасную причину ошибки;
- возраст самого старого необработанного candidate;
- состояние Telegram/LLM, если события delivery сохраняются.

Health indicator раскрывается в список `integration_poll_runs`. Строка запуска
содержит trigger, начало, окончание, длительность, счётчики, статус и безопасный
`error_code`. OAuth tokens, URLs с токенами и тело письма не показываются.

## 8. Drill-down и таблицы

### 8.1 Общие требования

- side panel для быстрого просмотра или отдельный route для полного списка;
- pagination и сортировка по времени;
- заголовок с количеством строк, периодом, account и фильтрами;
- пустой результат объясняет применённые фильтры;
- фильтры сохраняются в URL;
- aggregate и drill-down используют один и тот же filter contract;
- полный текст письма не загружается по умолчанию.

### 8.2 Таблица писем

Столбцы:

- `received_at`, `processed_at`;
- sender и subject;
- безопасная ссылка на Gmail;
- classification, confidence и применённый classification threshold;
- сработавшее пользовательское правило классификации, если оно есть;
- processing status;
- filter decision или error code;
- candidate status;
- ссылка на задачу, если она создана.

### 8.3 Candidate detail

Показывает:

- исходное письмо;
- classification, confidence и candidate payload;
- lifecycle `PENDING → NOTIFIED → CONFIRMED/REJECTED/EXPIRED`;
- `detected_at`, `notified_at`, `decided_at`;
- decision reason;
- связанную задачу.

### 8.4 Task detail

Показывает task id, title, type, status, priority, deadline, created_at и
связанный Gmail source. Должна быть обратная ссылка на исходное письмо.

## 9. Изменения в модели данных

### 9.1 `integration_poll_runs`

Добавить durable-запись на каждый запуск polling:

- `id`;
- `user_id`, `provider`, `account_id`;
- `trigger`: `scheduled`, `manual_api`, `telegram`;
- `started_at`, `finished_at`, `status`;
- `fetched_count`, `stored_count`, `duplicate_count`;
- `processed_count`, `ignored_count`, `candidate_count`, `failed_count`;
- `notified_count`;
- безопасные `error_code` и `error_message`.

### 9.2 `task_candidates`

Нормализовать candidate lifecycle:

- `id`, `user_id`, `source_message_id` с уникальностью на source message;
- `classification`, `confidence`, `payload`;
- `status`: `PENDING`, `NOTIFIED`, `CONFIRMED`, `REJECTED`, `EXPIRED`;
- `detected_at`, `notified_at`, `decided_at`;
- `decision_reason`, `task_id`.

`source_messages.metadata.candidate` можно сохранить для обратной совместимости,
но dashboard читает lifecycle из `task_candidates`.

### 9.3 Дополнительные поля и индексы

- Заполнять `source_messages.processed_at` во всех терминальных исходах.
- Фиксировать `notified_at` только после успешного Telegram API response.
- Сохранять notification error отдельно от LLM processing error.
- Индексы: по user/source/received_at, user/status/created_at,
  `task_sources.source_message_id`, user/created_at для events и account/started_at
  для poll runs.
- Для старых записей возвращать `data_quality=partial`, не реконструировать
  отсутствующие timestamps.

## 10. API-контракт

Рекомендуемые endpoints:

```text
GET /dashboard/overview
GET /dashboard/timeseries
GET /dashboard/messages
GET /dashboard/tasks
GET /dashboard/candidates
GET /dashboard/candidates/{candidate_id}
GET /dashboard/operations
GET /dashboard/poll-runs
GET /dashboard/gmail/settings
PUT /dashboard/gmail/settings
```

Общие параметры: `from`, `to`, `timezone`, `account_id`, `limit`, `cursor` и
доменные filters. `user_id` из браузера не принимается.

Ответ `overview` содержит:

```json
{
  "generated_at": "2026-08-14T12:00:00Z",
  "period": {"from": "...", "to": "...", "timezone": "Europe/Moscow"},
  "data_freshness": {"last_successful_poll": "...", "state": "FRESH"},
  "data_quality": "COMPLETE",
  "metrics": [],
  "funnel": [],
  "health": {}
}
```

Каждый KPI и funnel node содержит:

- `value`;
- `label` и `definition`;
- `count_basis`;
- применённые `filters`;
- `drilldown` с route или стабильным query state;
- `data_quality`.

Списки используют pagination и не допускают N+1 запросов на строку.

## 11. Авторизация и безопасность

- Dashboard доступен только владельцу.
- Все endpoints используют текущий owner auth context.
- Постоянный internal bearer token не встраивается во frontend JavaScript.
- Для web-доступа использовать короткоживущую сессию или auth на reverse proxy.
- API не отдаёт access/refresh tokens, OAuth metadata, секреты и полный текст
  писем по умолчанию.
- Ошибки нормализуются в безопасные коды; внутренний traceback остаётся в
  защищённых серверных логах.
- Dashboard не получает права создавать, подтверждать или удалять задачи.

## 12. Производительность и качество

- Все aggregate queries должны быть owner-scoped и проверяться на планах SQL.
- Для списков обязательны pagination и максимальный `limit`.
- Overview не должен выполнять отдельный запрос на каждую карточку; агрегаты
  группируются в ограниченное число запросов.
- Время ответа overview для обычного периода — целевой показатель до 1 секунды
  на локальном VPS; точный SLO фиксируется после измерений.
- Графики не должны загружать все исходные строки.
- История polling и candidate lifecycle покрываются fixture-тестами.

## 13. Этапы реализации

### Этап A — метрики и схема

- Утвердить определения KPI и временные оси.
- Добавить migrations для `integration_poll_runs` и `task_candidates`.
- Заполнить lifecycle timestamps.
- Добавить fixture: письмо → обработка → candidate → Telegram → задача.

### Этап B — dashboard query layer

- Реализовать owner-scoped SQL/query services.
- Реализовать общий filter contract и `data_quality`.
- Покрыть агрегаты и drill-down contract tests.

### Этап C — read-only API

- Добавить endpoints overview, timeseries, messages, tasks, candidates и
  operations.
- Добавить pagination, timezone conversion и безопасные ошибки.
- Проверить производительность SQL и отсутствие N+1.

### Этап D — web UI

- Собрать overview с KPI, funnel и health.
- Добавить timeseries и drill-down таблицы.
- Добавить URL state, side panel и переходы письмо/candidate/task.
- Добавить ручное обновление данных; ручной poll оставить отдельным явно
  подтверждаемым действием.

### Этап E — выпуск

- Прогнать полный тестовый набор.
- Проверить auth, sensitive-log review и отсутствие секретов в API response.
- Выполнить read-only smoke check.
- Зафиксировать ограничения исторических данных и rollback-план.

## 14. Критерии приёмки

- Повторный polling одного Gmail message не увеличивает counts и candidates.
- Для fixture-сценария dashboard показывает одну запись на каждый переход и одну
  связанную задачу.
- Каждый видимый KPI, funnel stage, график и health indicator имеет drill-down.
- Drill-down сохраняет фильтры и возвращает тот же count, что aggregate.
- `IGNORED` не считается LLM error.
- Ошибки LLM и Telegram delivery разделены.
- При отсутствии poll history отображается `data_quality=PARTIAL` и предупреждение.
- UTC и timezone пользователя корректно работают на границах суток.
- Все данные owner-scoped.
- API не выполняет N+1 запросы на drill-down.
- В API нет токенов и полного текста писем по умолчанию.
- Stale polling отображается как деградация даже при `CONNECTED`.
- Dashboard не создаёт и не изменяет задачи без отдельного подтверждённого
  действия, включённого вне read-only MVP.

## 15. Решения, требующие утверждения

Перед началом реализации нужно утвердить:

1. `docs/proposals/` как место для этого ТЗ до согласования.
2. Jinja2 + HTMX как MVP UI вместо отдельного React-приложения.
3. Сессию/reverse-proxy auth для браузера вместо постоянного bearer token.
4. Набор миграций `integration_poll_runs` и `task_candidates`.
5. Включать ли кнопку ручного Gmail poll в первый релиз или оставить только
   ссылку на существующий операторский endpoint.
