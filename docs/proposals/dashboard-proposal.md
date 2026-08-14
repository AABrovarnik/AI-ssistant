# Предложение по дашборду AI Secretary

Дата: `2026-08-14`

## 1. Цель

Дашборд должен отвечать на четыре практических вопроса владельца:

1. Приходят ли письма и когда система в последний раз их проверяла?
2. Сколько писем система увидела, обработала, отфильтровала или не смогла обработать?
3. Сколько писем стали кандидатами, сколько дошло до Telegram и сколько было подтверждено как задачи?
4. Работает ли цепочка Gmail → LLM → Telegram и где именно она остановилась?

Это должен быть owner-only web dashboard с read-only аналитикой. Для текущего single-user продукта не требуется сразу строить multi-user workspace, роли и сложную BI-платформу.

Ключевое требование интерфейса: любой показатель должен раскрываться до списка исходных объектов. Пользователь не должен видеть число без возможности понять, какие именно письма, запуски, candidate или задачи в него попали.

## 2. Что уже есть в системе

### Факты

- `source_messages` хранит Gmail-сообщение, отправителя, тему, время получения, статус обработки, классификацию, confidence, код ошибки и metadata.
- Gmail использует статусы `NEW`, `PROCESSING`, `PROCESSED`, `IGNORED`, `FAILED`.
- Решение Gmail-фильтра хранится в `metadata.gmail.filter_decision`.
- Candidate пока хранится в `source_messages.metadata.candidate`.
- Подтверждённая задача связывается с письмом через `task_sources` с relation `CREATED_FROM`.
- Создание задачи аудируется через `task_events` с событием `TASK_CREATED`.
- Задачи имеют тип, статус, приоритет, сроки и `created_at`.
- У Gmail account сейчас есть только `last_polled_at`, статус и последняя ошибка.
- `GmailSyncResult` содержит счётчики одного запуска только в памяти; история запусков не сохраняется.
- Поле `source_messages.processed_at` существует, но текущий Gmail flow его не заполняет, поэтому надёжную latency-метрику пока построить нельзя.
- В проекте ещё нет dashboard API или web UI. В текущем roadmap dashboard отнесён к более позднему product-depth этапу.

### Ограничения текущих данных

Нельзя честно показывать «все письма, пришедшие в Gmail», если система видит их только через polling. Сейчас корректная формулировка — «письма, обнаруженные и сохранённые системой». Точный график запусков, длительность обработки, доставка candidate в Telegram и её задержка требуют отдельной фиксации событий.

## 3. Предлагаемый экран MVP

### Верхняя панель

- период: сегодня, 7 дней, 30 дней, произвольный диапазон;
- часовой пояс пользователя, при этом хранение и расчёты в UTC;
- Gmail account/provider;
- время формирования данных;
- freshness: последний успешный polling, возраст последней проверки и флаг неполного периода.

### KPI-карточки

| Показатель | Определение для MVP |
|---|---|
| Писем обнаружено | `COUNT(DISTINCT source_messages.id)` для `source_type=GMAIL` по `received_at` |
| Обработано успешно | `processing_status=PROCESSED` |
| Отфильтровано | `processing_status=IGNORED` и `metadata.gmail.filter_decision=IGNORE` |
| Ошибок обработки | `processing_status=FAILED` |
| Candidate найдено | наличие `metadata.candidate` |
| Candidate ожидают решения | candidate без связанной задачи и без финального решения пользователя |
| Создано задач из Gmail | distinct `task_sources.task_id` с relation `CREATED_FROM` |
| Конверсия в задачи | создано задач / количество candidate |

Для карточек должны быть видны и абсолютное значение, и изменение к предыдущему сопоставимому периоду. Клик по карточке открывает drill-down с теми же фильтрами периода и account.

### Основной funnel

```text
Обнаружено → Сохранено → Обработано
                     ├→ Отфильтровано
                     ├→ Ошибка
                     └→ Candidate → Отправлено в Telegram → Подтверждено как задача
```

В funnel нельзя смешивать разные временные оси. Письма считаются по `received_at`, задачи — по `task_sources.created_at` или `tasks.created_at`, polling — по времени запуска. В API рядом с каждым блоком нужно возвращать `count_basis` и период расчёта.

Каждый этап funnel кликабелен и раскрывается до строк исходных данных. Например, этап `Ошибки` показывает конкретные письма и безопасный `error_code`, а этап `Создано задач` — конкретные задачи с Gmail source link.

### Графики

- динамика писем, candidate и созданных задач по дням;
- распределение писем: `PROCESSED`, filtered, failed;
- распределение candidate по типу: `MY_TASK`, `DELEGATED`, `AWAITING`;
- распределение созданных задач по статусу и приоритету;
- среднее и p95 время от получения письма до обработки — после появления `processed_at` и poll history.

Каждая точка/столбец графика также является фильтром: клик по дню, типу, статусу или priority открывает соответствующие письма или задачи. При переходе между экранами фильтры должны сохраняться в URL, чтобы ссылку можно было воспроизвести и отправить оператору.

### Операционный блок

Минимальный блок здоровья должен показывать:

- Gmail status: `CONNECTED`, `ERROR`, `DISCONNECTED`;
- последний успешный poll и следующий ожидаемый poll;
- число новых писем с последней успешной проверки;
- количество ошибок за период и последняя безопасная причина ошибки;
- возраст самого старого необработанного candidate;
- статус LLM/Telegram delivery, если эти события будут сохраняться.

Красный статус должен означать не только падение процесса, но и stale data: например, `CONNECTED`, но polling не выполнялся дольше допустимого интервала.

Статус health раскрывается в журнал запусков polling и ошибок. Для каждого запуска показываются trigger, начало/окончание, длительность, счётчики и безопасная причина failure. Нельзя подменять историю запусков текущим `last_polled_at`.

### Таблица drill-down

Строка письма должна включать:

- время получения и время обработки;
- sender, subject и Gmail link;
- классификацию и confidence;
- текущий processing status;
- filter decision или error code;
- состояние candidate: pending, confirmed, rejected, expired;
- ссылку на созданную задачу, если она есть.

Полный текст письма не показывать в списке и не отдавать без отдельного действия. Токены и OAuth metadata никогда не должны попадать в dashboard API.

## 3.1. Единый drill-down контракт

Разворачивание должно работать одинаково для всех элементов дашборда:

| Откуда раскрываем | Что открываем | Минимальная детализация |
|---|---|---|
| KPI «Писем обнаружено» | список `source_messages` | Gmail id, received_at, sender, subject, status |
| KPI «Обработано» | обработанные письма | classification, confidence, processed_at |
| KPI «Отфильтровано» | filtered письма | filter decision, sender, причина |
| KPI «Ошибок» | failed письма и/или poll runs | error_code, время, retry/next action |
| KPI «Candidate» | `task_candidates` | lifecycle status, confidence, notified/decided timestamps |
| KPI «Задач из Gmail» | задачи и `task_sources` | task id, title, type, status, created_at, source |
| Любой этап funnel | соответствующий набор этапа | count, applied filters, source of count |
| Точка графика | набор за bucket времени | тот же список с добавленным bucket filter |
| Health-карточка | poll runs / integration events | статус, длительность, counters, error |

Поведение UI:

- клик открывает side panel для быстрого просмотра или отдельный route для полноценного списка;
- в заголовке показываются число строк, период, account и применённые фильтры;
- из строки письма можно открыть Gmail, candidate или связанную задачу;
- из строки задачи можно вернуться к исходному письму;
- для пустого результата показывается не только `0`, но и объяснение фильтров;
- списки имеют pagination и сортировку по времени, но не загружают полный текст письма по умолчанию;
- aggregate response должен возвращать `drilldown_query`/стабильный filter state, чтобы UI не пересчитывал смысл показателя самостоятельно.

Пример: клик по «Candidate: 12» открывает список из 12 candidate. Клик по одному candidate показывает исходное письмо, confidence, текст candidate, историю `PENDING → NOTIFIED → CONFIRMED` и ссылку на задачу. Клик по задаче возвращает в общий task history.

## 4. Что добавить в модель данных

### Обязательно для достоверной аналитики

#### `integration_poll_runs`

Одна запись на каждый запуск polling:

- `id`, `user_id`, `provider`, `account_id`;
- `trigger`: `scheduled`, `manual_api`, `telegram`;
- `started_at`, `finished_at`, `status`;
- `fetched_count`, `stored_count`, `duplicate_count`, `processed_count`;
- `ignored_count`, `candidate_count`, `failed_count`, `notified_count`;
- безопасный `error_code`/`error_message` без токенов и тела письма.

Это позволит отличать «в Gmail было письмо» от «система его обнаружила», видеть пропущенные polling runs и считать latency.

#### `task_candidates`

Нормализованная сущность вместо аналитически непрозрачного флага в JSON:

- `id`, `user_id`, `source_message_id` — unique;
- `classification`, `confidence`, `payload`;
- `status`: `PENDING`, `NOTIFIED`, `CONFIRMED`, `REJECTED`, `EXPIRED`;
- `detected_at`, `notified_at`, `decided_at`;
- `decision_reason`, `task_id`.

Существующее `source_messages.metadata.candidate` можно сохранить для обратной совместимости, но dashboard должен читать lifecycle из этой таблицы.

### Рекомендуемые технические изменения

- Заполнять `source_messages.processed_at` во всех успешных и ошибочных терминальных исходах.
- Явно фиксировать `notified_at` только после успешного Telegram API response.
- При `ignore`, `edit`, `create` записывать переход candidate lifecycle и actor.
- Добавить индексы по `(user_id, source_type, received_at)`, `(user_id, processing_status, created_at)`, `task_sources.source_message_id`, `(user_id, created_at)` для `task_events` и `(account_id, started_at)` для poll runs.
- Для старых записей показывать `data_quality=partial`, а не реконструировать ложные времена.

Таблицу уведомлений (`notification_deliveries`) лучше добавить отдельным шагом, когда Telegram, digest и reminders будут сведены к единому delivery layer. Для Gmail MVP достаточно полей `notified_at` и `notification_error` в candidate lifecycle.

## 5. API и UI

Рекомендованный порядок — сначала read-only API, затем тонкий web UI:

- `GET /dashboard/overview?from=&to=&timezone=` — KPI, funnel, freshness и health;
- `GET /dashboard/timeseries?from=&to=&bucket=day` — динамика;
- `GET /dashboard/messages?...` — drill-down с фильтрами и пагинацией;
- `GET /dashboard/tasks?...` — задачи, созданные из Gmail, и их статусы;
- `GET /dashboard/operations` — poll runs, ошибки и состояние интеграций;
- `GET /dashboard/candidates/{candidate_id}` — lifecycle и связанные письмо/задача;
- `GET /dashboard/poll-runs?...` — drill-down запусков polling и ошибок.

Каждый endpoint должен автоматически ограничивать данные владельцем из текущего auth context. Не принимать произвольный `user_id` от браузера. Для browser access нужен короткоживущий authenticated session или reverse-proxy auth; не встраивать постоянный internal bearer token в frontend.

Ответ overview должен содержать:

- `generated_at`;
- `period` и `timezone`;
- `data_freshness`;
- `metrics`;
- `funnel`;
- `data_quality` и список известных ограничений.

Для каждого KPI/funnel node рекомендуется возвращать:

- `value`;
- `label` и `definition`;
- `count_basis`;
- `filters`;
- `drilldown` с route или стабильным query state;
- `data_quality`.

На первом этапе не нужен Grafana: Grafana хорошо подходит для инфраструктурных time-series, но не заменяет пользовательский funnel с drill-down и безопасной работой с Gmail metadata.

## 6. Этапы реализации

### Этап A — контракт метрик

- зафиксировать определения и временные оси;
- добавить SQL/query service без UI;
- покрыть fixture-тестом сценарий: письмо → обработка → candidate → Telegram → подтверждённая задача;
- проверить idempotency повторного polling.

### Этап B — наблюдаемость pipeline

- migration для `integration_poll_runs` и `task_candidates`;
- заполнение `processed_at`, notification и decision timestamps;
- безопасная обработка ошибок и stale-data calculation.

### Этап C — dashboard API

- owner-scoped read-only endpoints;
- пагинация, фильтры, timezone conversion;
- contract tests и проверка SQL-производительности.

### Этап D — web UI

- overview/funnel;
- operations health;
- drill-down письма и задач;
- drill-down для каждого KPI, funnel stage, графика и health-состояния;
- ручное обновление и ссылка на ручную проверку Gmail.

## 7. Критерии готовности

- Повторная проверка одного Gmail message не увеличивает «новые письма» и candidate.
- Для fixture-сценария dashboard показывает ровно одну запись на каждом переходе и одну задачу.
- Каждый видимый KPI, funnel stage, график и health indicator раскрывается до конкретных исходных записей.
- Фильтры drill-down сохраняются при переходе и дают тот же count, что был показан в aggregate.
- Отфильтрованные письма не считаются ошибками LLM.
- Ошибка LLM и ошибка Telegram delivery видны раздельно.
- Для периода без сохранённой истории polling показывается предупреждение о неполных данных.
- Границы суток проверены в UTC и часовом поясе пользователя.
- Все агрегаты user-scoped; SQL не делает N+1 запросов на drill-down.
- В dashboard и логах нет access/refresh tokens и полного текста писем по умолчанию.
- Если Gmail stale, это отражается в health даже при `IntegrationAccount.status=CONNECTED`.

## Итоговая рекомендация

Делать дашборд как операционно-продуктовый экран для одной цепочки Gmail → LLM → Telegram → Task. Первый полезный vertical slice — не красивый график, а достоверный funnel с freshness и drill-down. Для этого сначала нужно сохранить poll runs и candidate lifecycle, затем поверх них строить API и UI.
