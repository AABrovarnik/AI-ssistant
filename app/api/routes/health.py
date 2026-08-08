import sqlite3

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import engine

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready() -> dict[str, str]:
    try:
        database_url = make_url(get_settings().database_url)
        if database_url.drivername.startswith("sqlite"):
            with sqlite3.connect(database_url.database or ":memory:") as connection:
                connection.execute("SELECT 1")
        else:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc

    return {"status": "ok"}
