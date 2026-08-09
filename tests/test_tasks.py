from uuid import uuid4

import httpx
import pytest
from app.main import app


@pytest.mark.asyncio
async def test_task_create_is_idempotent_and_complete_is_auditable() -> None:
    transport = httpx.ASGITransport(app=app)
    payload = {"title": "Pay invoice", "idempotency_key": "create-invoice-1"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/tasks", json=payload)
        second = await client.post("/tasks", json=payload)
        task_id = first.json()["id"]
        completed = await client.post(
            f"/tasks/{task_id}/complete", headers={"Idempotency-Key": "complete-invoice-1"}
        )
        repeated = await client.post(
            f"/tasks/{task_id}/complete", headers={"Idempotency-Key": "complete-invoice-1"}
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert completed.json()["status"] == "done"
    assert repeated.json()["status"] == "done"


@pytest.mark.asyncio
async def test_task_filter_and_missing_task() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/tasks", params={"status": "open"})
        missing = await client.get(f"/tasks/{uuid4()}")

    assert response.status_code == 200
    assert missing.status_code == 404
