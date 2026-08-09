# AI Secretary v1.0 — Development Pack

Готовый пакет постановки задачи для Codex / Claude Code.

## Назначение

AI-секретарь контролирует:

- мои собственные задачи;
- поручения коллегам и близким;
- ожидания ответов, документов и решений;
- дедлайны и просрочки;
- задачи, обнаруженные в Telegram и Gmail;
- календарные события Google Calendar.

Ключевая модель:

1. `MY_TASK` — я должен.
2. `DELEGATED` — мне должны / я поручил.
3. `AWAITING` — я жду.

## Целевой стек

- VPS Ubuntu 24.04
- Docker / Docker Compose
- OpenClaw
- Python 3.12+
- FastAPI
- PostgreSQL 16+
- SQLite — только для локального/упрощённого режима
- SQLAlchemy 2.x
- Alembic
- Telegram Bot
- Gmail API
- Google Calendar API
- LLM через OpenClaw / совместимый provider

## Файлы пакета

- `MASTER_PROMPT.md` — главный промпт для Codex / Claude Code.
- `ARCHITECTURE.md` — архитектура и границы компонентов.
- `DATABASE_SCHEMA.md` — схема БД и бизнес-модель.
- `API.md` — REST API.
- `PROMPTS.md` — системные промпты LLM.
- `docker-compose.yml` — базовое production-like окружение.
- `.env.example` — шаблон переменных окружения.
- `IMPLEMENTATION_PLAN.md` — этапы разработки и критерии приёмки.

## Как начать

1. Создайте на VPS рабочую папку проекта.
2. Распакуйте этот пакет.
3. Откройте папку в Codex / Claude Code.
4. Дайте агенту команду:

   `Прочитай MASTER_PROMPT.md и все документы, на которые он ссылается. Начни с Phase 0 из IMPLEMENTATION_PLAN.md. Не переходи к следующей фазе, пока тесты и критерии приёмки текущей фазы не выполнены.`

5. Секреты не вставляйте в Markdown. Используйте `.env`.
6. Начните с Telegram + PostgreSQL + Task Core. Gmail и Calendar подключайте после стабилизации ядра.

## Важный архитектурный принцип

LLM понимает смысл текста, но не является системой учёта.

- LLM → классификация и извлечение структуры.
- Python/FastAPI → правила и действия.
- PostgreSQL → долговременная память и источник истины.
- Пользователь → подтверждение значимых внешних действий.
