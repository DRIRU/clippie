"""In-process job runner (barebone mode). Job state lives in the DB, so a restart
re-queues unfinished jobs; handlers are idempotent and skip stages whose
checkpoints already exist."""
from __future__ import annotations

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from .config import get_settings
from .db import session
from .models import Job

log = logging.getLogger("fragforge.jobs")

_handlers: dict[str, Callable] = {}
_pool: ThreadPoolExecutor | None = None


def handler(kind: str):
    def deco(fn):
        _handlers[kind] = fn
        return fn
    return deco


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=get_settings().max_workers, thread_name_prefix="ff-job")
    return _pool


def update(job_id: str, **fields) -> None:
    with session() as s:
        j = s.get(Job, job_id)
        if j:
            for k, v in fields.items():
                setattr(j, k, v)
            j.updated_at = datetime.now(timezone.utc)
            s.commit()


def enqueue(kind: str, asset_id: str | None = None, params: dict | None = None) -> str:
    with session() as s:
        j = Job(kind=kind, asset_id=asset_id, params=params or {})
        s.add(j)
        s.commit()
        job_id = j.id
    _executor().submit(_run, job_id)
    return job_id


def retry(job_id: str) -> bool:
    with session() as s:
        j = s.get(Job, job_id)
        if not j or j.status not in ("failed", "done"):
            return False
        j.status, j.error, j.progress = "queued", None, 0.0
        s.commit()
    _executor().submit(_run, job_id)
    return True


def _run(job_id: str) -> None:
    with session() as s:
        j = s.get(Job, job_id)
        if not j or j.status not in ("queued", "running"):
            return
        kind, asset_id, params = j.kind, j.asset_id, dict(j.params or {})
        j.status, j.attempts = "running", (j.attempts or 0) + 1
        s.commit()

    def progress(stage: str, frac: float | None = None):
        f = {"stage": stage}
        if frac is not None:
            f["progress"] = round(max(0.0, min(1.0, frac)), 3)
        update(job_id, **f)

    try:
        _handlers[kind](job_id, asset_id, params, progress)
        update(job_id, status="done", progress=1.0, stage="done")
    except Exception as e:  # noqa: BLE001 - job boundary
        log.error("job %s (%s) failed: %s\n%s", job_id, kind, e, traceback.format_exc())
        update(job_id, status="failed", error=str(e)[:1000])


def resume_unfinished() -> int:
    """Re-queue jobs interrupted by a restart."""
    with session() as s:
        ids = [j.id for j in s.query(Job).filter(Job.status.in_(("queued", "running"))).all()]
    for i in ids:
        _executor().submit(_run, i)
    return len(ids)


def shutdown() -> None:
    global _pool
    if _pool:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
