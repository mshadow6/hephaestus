import asyncio
import time

from rq import Worker
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.connections.store import list_connections
from app.connections.testing import test_connection
from app.queue import redis_conn


def check_postgres(db: Session) -> dict:
    start = time.monotonic()
    try:
        db.execute(text("SELECT 1"))
        ok = True
        message = "OK"
    except Exception as exc:  # noqa: BLE001 — health check, on veut tout capter
        ok = False
        message = str(exc)
    return {"name": "PostgreSQL", "ok": ok, "message": message, "latency_ms": _ms(start)}


def check_redis() -> dict:
    start = time.monotonic()
    try:
        redis_conn.ping()
        ok = True
        message = "OK"
    except Exception as exc:  # noqa: BLE001
        ok = False
        message = str(exc)
    return {"name": "Redis", "ok": ok, "message": message, "latency_ms": _ms(start)}


def check_worker() -> dict:
    start = time.monotonic()
    try:
        workers = Worker.all(connection=redis_conn)
        ok = len(workers) > 0
        message = f"{len(workers)} worker(s) actif(s)" if ok else "Aucun worker connecté"
    except Exception as exc:  # noqa: BLE001
        ok = False
        message = str(exc)
    return {"name": "Worker RQ", "ok": ok, "message": message, "latency_ms": _ms(start)}


async def check_connectors() -> list[dict]:
    connections = list_connections()
    if not connections:
        return []

    async def _check(conn):
        start = time.monotonic()
        success, message = await test_connection(conn)
        return {
            "name": conn.name,
            "type": conn.type,
            "ok": success,
            "message": message,
            "latency_ms": _ms(start),
        }

    return await asyncio.gather(*(_check(c) for c in connections if c.enabled))


def _ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)
