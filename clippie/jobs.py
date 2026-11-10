"""In-process job runner (barebone mode). Job state lives in the DB, so a restart
re-queues unfinished jobs; handlers are idempotent and skip stages whose
checkpoints already exist."""
from __future__ import annotations

import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm.exc import StaleDataError

from . import perf
from .config import get_settings
from .db import session
from .models import Job

log = logging.getLogger("clippie.jobs")

class Cancelled(Exception):
    """Raised inside a running job when its video was deleted."""


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
    try:
        with session() as s:
            j = s.get(Job, job_id)
            if j:
                for k, v in fields.items():
                    setattr(j, k, v)
                j.updated_at = datetime.now(timezone.utc)
                s.commit()
    except StaleDataError:
        pass  # the job row was deleted along with its video


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
    try:
        with session() as s:
            j = s.get(Job, job_id)
            if not j or j.status not in ("queued", "running"):
                return
            kind, asset_id, params = j.kind, j.asset_id, dict(j.params or {})
            queued_since = j.updated_at or j.created_at
            j.status, j.attempts = "running", (j.attempts or 0) + 1
            attempts = j.attempts
            s.commit()
    except StaleDataError:
        return

    started = time.perf_counter()
    perf_status = "ok"
    try:
        qs = queued_since if queued_since.tzinfo else queued_since.replace(tzinfo=timezone.utc)
        queue_wait = max(0.0, (datetime.now(timezone.utc) - qs).total_seconds())
    except Exception:  # noqa: BLE001
        queue_wait = None
    perf.set_context(job_id=job_id, asset_id=asset_id)
    perf.job_started()

    last_check = [0.0]

    def progress(stage: str, frac: float | None = None):
        # Cheap, throttled check so deleting a video stops its running job (download, OCR, ...)
        if asset_id and time.monotonic() - last_check[0] > 1.0:
            last_check[0] = time.monotonic()
            if not _asset_exists(asset_id):
                raise Cancelled(asset_id)
        f = {"stage": stage}
        if frac is not None:
            f["progress"] = round(max(0.0, min(1.0, frac)), 3)
        update(job_id, **f)

    try:
        _handlers[kind](job_id, asset_id, params, progress)
        update(job_id, status="done", progress=1.0, stage="done")
    except Cancelled:
        perf_status = "cancelled"
        log.info("job %s (%s) cancelled: video was deleted", job_id, kind)
    except Exception as e:  # noqa: BLE001 - job boundary
        if asset_id and not _asset_exists(asset_id):
            perf_status = "cancelled"
            log.info("job %s (%s) stopped: video was deleted", job_id, kind)
        else:
            perf_status = "error"
            log.error("job %s (%s) failed: %s\n%s", job_id, kind, e, traceback.format_exc())
            update(job_id, status="failed", error=str(e)[:1000])
    finally:
        notes = perf.job_notes()
        perf.record(f"job.{kind}", time.perf_counter() - started, media_seconds=notes.pop("media_seconds", None),
                    status=perf_status, job_id=job_id, asset_id=asset_id,
                    detail={"queue_wait_s": None if queue_wait is None else round(queue_wait, 3), "attempt": attempts, **notes})
        perf.job_finished()
        perf.clear_context()
        if asset_id and not _asset_exists(asset_id):
            _purge_files(asset_id)  # the job may have written files after the delete ran


def _asset_exists(asset_id: str) -> bool:
    from .models import MediaAsset

    with session() as s:
        return s.get(MediaAsset, asset_id) is not None


def _purge_files(asset_id: str) -> None:
    from .storage import get_storage

    st = get_storage()
    for key in (f"sources/{asset_id}", f"thumbs/{asset_id}.jpg", f"analysis/{asset_id}"):
        try:
            st.delete(key)
        except OSError:
            pass


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
