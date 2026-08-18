from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.api.routes import tasks as task_routes
from app.core.config import get_settings
from app.main import app

AUTH = {"Authorization": f"Bearer {get_settings().internal_api_token}"}


@pytest.mark.asyncio
async def test_task_create_is_idempotent_and_complete_is_auditable() -> None:
    transport = httpx.ASGITransport(app=app)
    payload = {"title": "Pay invoice", "idempotency_key": "create-invoice-1"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/tasks", json=payload, headers=AUTH)
        second = await client.post("/tasks", json=payload, headers=AUTH)
        task_id = first.json()["id"]
        completed = await client.post(
            f"/tasks/{task_id}/complete",
            headers={**AUTH, "Idempotency-Key": "complete-invoice-1"},
        )
        repeated = await client.post(
            f"/tasks/{task_id}/complete",
            headers={**AUTH, "Idempotency-Key": "complete-invoice-1"},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert completed.json()["status"] == "done"
    assert repeated.json()["status"] == "done"


@pytest.mark.asyncio
async def test_overdue_new_task_can_be_completed() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/tasks",
            json={
                "title": "Overdue personal task",
                "due_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                "idempotency_key": f"overdue-complete-{uuid4()}",
            },
            headers=AUTH,
        )
        completed = await client.post(
            f"/tasks/{created.json()['id']}/complete",
            headers={**AUTH, "Idempotency-Key": f"complete-overdue-{uuid4()}"},
        )

    assert created.status_code == 201
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"


@pytest.mark.asyncio
async def test_task_filter_and_missing_task() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/tasks", params={"status": "open"}, headers=AUTH)
        missing = await client.get(f"/tasks/{uuid4()}", headers=AUTH)

    assert response.status_code == 200
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_task_create_supports_unknown_party_status() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/tasks",
            json={
                "title": "Получить расчёт",
                "task_type": "AWAITING",
                "status": "unknown_party",
                "idempotency_key": "unknown-party-task-1",
            },
            headers=AUTH,
        )

    assert response.status_code == 201
    assert response.json()["status"] == "unknown_party"


@pytest.mark.asyncio
async def test_task_api_requires_internal_token() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/tasks")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_task_update_refreshes_response_after_calendar_sync(monkeypatch) -> None:
    async def expire_updated_at(task, session) -> None:
        session.expire(task, ["updated_at"])

    monkeypatch.setattr(task_routes, "_sync_linked_calendar", expire_updated_at)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/tasks",
            json={
                "title": "Calendar response refresh",
                "due_at": "2026-08-14T10:00:00Z",
                "idempotency_key": "calendar-response-refresh-1",
            },
            headers=AUTH,
        )
        task_id = created.json()["id"]
        updated = await client.patch(
            f"/tasks/{task_id}",
            json={"due_at": "2026-08-14T11:00:00Z"},
            headers={**AUTH, "Idempotency-Key": "calendar-response-refresh-2"},
        )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["due_at"].startswith("2026-08-14T11:00:00")
