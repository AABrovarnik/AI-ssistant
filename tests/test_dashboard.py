from datetime import UTC, datetime

import httpx
import pytest
from app.core.config import get_settings
from app.db.session import session_factory
from app.main import app
from app.tasks.models import (
    CandidateStatus,
    IntegrationAccount,
    IntegrationPollRun,
    IntegrationProvider,
    PollRunStatus,
    PollRunTrigger,
    TaskCandidate,
    UserSettings,
)
from app.tasks.schemas import SourceMessageCreate, TaskCreate
from app.tasks.service import TaskService
from sqlalchemy import select

AUTH = {"Authorization": f"Bearer {get_settings().internal_api_token}"}
PERIOD = {"from": "2026-08-14", "to": "2026-08-14", "timezone": "UTC"}


@pytest.mark.asyncio
async def test_dashboard_login_and_owner_access() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/dashboard")
        login_page = await client.get("/dashboard/login")
        invalid = await client.post("/dashboard/login", json={"token": "wrong"})
        login = await client.post(
            "/dashboard/login", json={"token": get_settings().internal_api_token}
        )
        authenticated_page = await client.get("/dashboard")

    assert page.status_code == 303
    assert page.headers["location"] == "/dashboard/login"
    assert login_page.status_code == 200
    assert invalid.status_code == 401
    assert login.status_code == 200
    assert authenticated_page.status_code == 200
    assert "AI Secretary dashboard" in authenticated_page.text


@pytest.mark.asyncio
async def test_dashboard_overview_matches_drilldown() -> None:
    observed_at = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    async with session_factory() as session:
        service = TaskService(session)
        user = await service.ensure_user()
        account = IntegrationAccount(
            user_id=user.id,
            provider=IntegrationProvider.GMAIL,
            access_token_encrypted="encrypted",
            last_polled_at=observed_at,
        )
        session.add(account)
        settings = await session.get(UserSettings, user.id)
        assert settings is not None
        source = await service.create_source_message(
            SourceMessageCreate(
                source_type="GMAIL",
                external_id="dashboard-message-1",
                sender_email="sender@example.com",
                sender_name="Sender",
                subject="Dashboard task",
                received_at=observed_at,
                source_url="https://mail.google.com/mail/u/0/#inbox/dashboard-message-1",
            ),
            user.id,
        )
        source.processing_status = "PROCESSED"
        source.classification = "TASK"
        source.confidence = 0.95
        source.processed_at = observed_at
        candidate = TaskCandidate(
            user_id=user.id,
            source_message_id=source.id,
            classification="TASK",
            confidence=0.95,
            payload={"title": "Dashboard task", "task_type": "MY_TASK"},
            status=CandidateStatus.CONFIRMED,
            detected_at=observed_at,
            notified_at=observed_at,
            decided_at=observed_at,
        )
        session.add(candidate)
        run = IntegrationPollRun(
            user_id=user.id,
            provider=IntegrationProvider.GMAIL,
            account_id=account.id,
            trigger=PollRunTrigger.SCHEDULED,
            started_at=observed_at,
            finished_at=observed_at,
            status=PollRunStatus.SUCCEEDED,
            fetched_count=1,
            stored_count=1,
            processed_count=1,
            candidate_count=1,
            notified_count=1,
        )
        session.add(run)
        await session.commit()
        task = await service.create(
            TaskCreate(
                title="Dashboard task",
                source_type="GMAIL",
                source_id=source.external_id,
                source_message_id=source.id,
                user_id=user.id,
                idempotency_key="dashboard-task-1",
            )
        )
        candidate = await session.scalar(
            select(TaskCandidate).where(TaskCandidate.source_message_id == source.id)
        )
        assert candidate is not None
        candidate.task_id = task.id
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        overview = await client.get("/dashboard/overview", params=PERIOD, headers=AUTH)
        timeseries = await client.get("/dashboard/timeseries", params=PERIOD, headers=AUTH)
        messages = await client.get("/dashboard/messages", params=PERIOD, headers=AUTH)
        candidates = await client.get("/dashboard/candidates", params=PERIOD, headers=AUTH)
        tasks = await client.get("/dashboard/tasks", params=PERIOD, headers=AUTH)

    assert overview.status_code == 200
    metrics = {item["key"]: item["value"] for item in overview.json()["metrics"]}
    assert metrics["messages_detected"] == 1
    assert metrics["candidates"] == 1
    assert metrics["tasks_from_gmail"] == 1
    assert metrics["task_conversion"] == 1.0
    assert overview.json()["health"]["state"] in {"FRESH", "STALE"}
    assert timeseries.status_code == 200
    assert timeseries.json()["points"][0]["messages"] == 1
    assert len(messages.json()["items"]) == 1
    assert len(candidates.json()["items"]) == 1
    assert len(tasks.json()["items"]) == 1
