# AI Secretary

Telegram-first personal AI secretary for tasks, delegated commitments and awaiting results.

The implementation follows the reviewed development pack:

- PostgreSQL is the source of truth.
- LLM returns validated structured data and never writes to the database directly.
- External Telegram/Gmail content is untrusted input.
- Significant actions are auditable and idempotent.

## Local setup

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD and INTERNAL_API_TOKEN in .env
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
uvicorn app.main:app --reload
```

## Docker setup

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD in .env
docker compose up -d --build
curl http://127.0.0.1:8000/health/live
```

Task Core provides durable tasks with statuses, priorities, deadlines and sources. Create operations are idempotent by `idempotency_key`; state-changing operations require `Idempotency-Key` and append an audit event in the same transaction.

Endpoints: `POST /tasks`, `GET /tasks`, `GET /tasks/{id}`, `PATCH /tasks/{id}` and `POST /tasks/{id}/complete`. Task endpoints require `Authorization: Bearer $INTERNAL_API_TOKEN`.

Phase 3 adds a provider-neutral structured LLM parser. Free-form Telegram text is
classified and extracted into a validated task candidate; it is shown as a
preview and is not written to the database yet. The parser supports one JSON
repair retry and treats incoming message text as untrusted data. Enable the
OpenClaw OpenAI-compatible chat-completions endpoint before using this flow.
