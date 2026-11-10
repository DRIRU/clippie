"""Performance log: timed stages, stored in SQLite so you can measure and compare later.

Every job and every pipeline stage records one row in `perf_events`:

    stage          e.g. "analyze.motion", "export.render", "job.analyze"
    seconds        wall-clock time of the stage
    media_seconds  how much video/clip it processed, so  media_seconds / seconds  = x real time
    concurrent     how many jobs were running at that moment (timings under load include contention)
    detail         stage specific facts (kind, resolution, model, bytes, ...)

Each server start creates a `perf_runs` row holding the settings and hardware, and every event points at
its run, so results from different settings (workers, NVENC, models) can be compared.

Logging must never break processing: every write is wrapped and failures are only logged.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .config import get_settings
from .db import session
from .models import PerfEvent, PerfRun

log = logging.getLogger("clippie.perf")

_run_id: str | None = None
_run_lock = threading.Lock()
_ctx = threading.local()
_running = 0
_running_lock = threading.Lock()


# ---------------------------------------------------------------- run metadata
def _cmd(args: list[str]) -> str | None:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _host() -> dict:
    from importlib import metadata

    h = {"platform": platform.platform(), "python": sys.version.split()[0], "cpu_count": os.cpu_count()}
    v = _cmd(["ffmpeg", "-version"])
    if v:
        h["ffmpeg"] = v.splitlines()[0][:120]
    gpu = _cmd(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])
    if gpu:
        h["gpu"] = gpu.splitlines()[0]
    for pkg in ("faster-whisper", "ctranslate2", "onnxruntime"):
        try:
            h[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    return h


def _config() -> dict:
    c = get_settings()
    return {k: getattr(c, k) for k in (
        "max_workers", "use_nvenc", "vertical_max_fps", "whisper_model", "whisper_device", "whisper_compute_type",
        "vlm_frames", "vlm_max_images", "auto_verify", "auto_captions", "candidate_padding_before",
        "candidate_padding_after", "min_candidate_score", "max_candidates_per_hour")} | {"vlm_enabled": bool(c.vlm_base_url)}


def start_run() -> str:
    """Start a new run (call once at server start). Safe to call again: it returns the existing run."""
    global _run_id
    with _run_lock:
        if _run_id is None:
            rid = str(uuid.uuid4())
            try:
                with session() as s:
                    s.add(PerfRun(run_id=rid, config=_config(), host=_host()))
                    s.commit()
            except Exception:  # noqa: BLE001 - logging must not stop the server
                log.exception("could not record the performance run")
            _run_id = rid
        return _run_id


def reset_run() -> None:
    """Forget the current run (used by tests / a fresh database)."""
    global _run_id
    with _run_lock:
        _run_id = None


def current_run_id() -> str:
    return _run_id or start_run()


# ---------------------------------------------------------------- context + concurrency
def set_context(**kw) -> None:
    _ctx.data = {k: v for k, v in kw.items() if v is not None}
    _ctx.note = {}


def clear_context() -> None:
    _ctx.data = {}
    _ctx.note = {}


def job_note(**kw) -> None:
    """Facts about the current job (e.g. media_seconds=<video length>) added to its job.* event."""
    if not hasattr(_ctx, "note"):
        _ctx.note = {}
    _ctx.note.update(kw)


def job_notes() -> dict:
    return dict(getattr(_ctx, "note", {}) or {})


def job_started() -> None:
    global _running
    with _running_lock:
        _running += 1


def job_finished() -> None:
    global _running
    with _running_lock:
        _running = max(0, _running - 1)


def jobs_running() -> int:
    return _running


# ---------------------------------------------------------------- recording
def record(stage: str, seconds: float, *, media_seconds: float | None = None, status: str = "ok",
           job_id: str | None = None, asset_id: str | None = None, candidate_id: str | None = None,
           detail: dict | None = None) -> None:
    if not get_settings().perf_log:
        return
    ctx = getattr(_ctx, "data", {}) or {}
    try:
        with session() as s:
            s.add(PerfEvent(
                run_id=current_run_id(), stage=stage, seconds=round(float(seconds), 4),
                media_seconds=None if media_seconds is None else round(float(media_seconds), 3), status=status,
                job_id=job_id or ctx.get("job_id"), asset_id=asset_id or ctx.get("asset_id"),
                candidate_id=candidate_id, concurrent=jobs_running(), detail=detail or {}))
            s.commit()
    except Exception:  # noqa: BLE001 - never let logging break the pipeline
        log.exception("could not record performance event %s", stage)


@contextmanager
def timer(stage: str, *, media_seconds: float | None = None, candidate_id: str | None = None,
          asset_id: str | None = None, job_id: str | None = None, **detail):
    """Time a block. Yields a dict so the block can add facts afterwards:

        with perf.timer("export.render", media_seconds=20, kind="vertical") as rec:
            ...
            rec["detail"]["bytes"] = size
    """
    rec = {"media_seconds": media_seconds, "detail": dict(detail)}
    t0 = time.perf_counter()
    status = "ok"
    try:
        yield rec
    except BaseException as e:
        status = "cancelled" if type(e).__name__ == "Cancelled" else "error"
        raise
    finally:
        record(stage, time.perf_counter() - t0, media_seconds=rec["media_seconds"], status=status,
               job_id=job_id, asset_id=asset_id, candidate_id=candidate_id, detail=rec["detail"])


_gpu_ok = True


def gpu_mem_used_mb() -> int | None:
    """Current GPU memory in use (NVIDIA only); None when unavailable. Cheap enough to call per clip."""
    global _gpu_ok
    if not _gpu_ok:
        return None
    out = _cmd(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
    if not out:
        _gpu_ok = False
        return None
    try:
        return int(out.splitlines()[0])
    except ValueError:
        return None


# ---------------------------------------------------------------- querying
_REL = re.compile(r"^(\d+)([smhdw])$")


def parse_when(v: str | None) -> datetime | None:
    """'30m', '24h', '7d', '2w' (relative to now), or an ISO date/time."""
    if not v or v == "all":
        return None
    m = _REL.match(v.strip())
    if m:
        unit = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
        return datetime.now(timezone.utc) - timedelta(seconds=int(m.group(1)) * unit)
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        raise ValueError(f"cannot read {v!r}: use 30m, 24h, 7d or an ISO date") from None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _filtered(q, since=None, until=None, run=None, asset_id=None, stage=None, status=None):
    if since:
        q = q.where(PerfEvent.ts >= since)
    if until:
        q = q.where(PerfEvent.ts <= until)
    if run == "current":
        q = q.where(PerfEvent.run_id == current_run_id())
    elif run and run != "all":
        q = q.where(PerfEvent.run_id == run)
    if asset_id:
        q = q.where(PerfEvent.asset_id == asset_id)
    if stage:
        q = q.where(PerfEvent.stage.like(stage.replace("%", "") + "%"))
    if status:
        q = q.where(PerfEvent.status == status)
    return q


def _event_dict(e: PerfEvent) -> dict:
    rtf = round(e.media_seconds / e.seconds, 2) if e.media_seconds and e.seconds > 0 else None
    return {"id": e.id, "ts": e.ts.isoformat() if e.ts else None, "run_id": e.run_id, "stage": e.stage,
            "seconds": e.seconds, "media_seconds": e.media_seconds, "realtime_x": rtf, "status": e.status,
            "concurrent": e.concurrent, "job_id": e.job_id, "asset_id": e.asset_id, "candidate_id": e.candidate_id,
            "detail": e.detail or {}}


def events(limit: int = 200, offset: int = 0, **filters) -> list[dict]:
    with session() as s:
        q = _filtered(select(PerfEvent), **filters).order_by(PerfEvent.id.desc()).limit(limit).offset(offset)
        return [_event_dict(e) for e in s.scalars(q).all()]


def all_events(**filters) -> list[dict]:
    with session() as s:
        q = _filtered(select(PerfEvent), **filters).order_by(PerfEvent.id)
        return [_event_dict(e) for e in s.scalars(q).all()]


def runs() -> list[dict]:
    with session() as s:
        rows = s.scalars(select(PerfRun).order_by(PerfRun.started_at.desc())).all()
        counts = dict(s.execute(select(PerfEvent.run_id, func.count()).group_by(PerfEvent.run_id)).all())
        return [{"run_id": r.run_id, "started_at": r.started_at.isoformat(), "events": counts.get(r.run_id, 0),
                 "config": r.config, "host": r.host, "current": r.run_id == _run_id} for r in rows]


def _group_value(ev: dict, key: str):
    if key == "run":
        return ev["run_id"]
    if key == "asset":
        return ev["asset_id"]
    if key.startswith("detail."):
        return ev["detail"].get(key[7:])
    raise ValueError(f"cannot group by {key!r}: use run, asset or detail.<name>")


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(rows: list[dict], group_by: list[str] | None = None) -> list[dict]:
    """Per stage (and optional detail keys): count, errors, mean / p50 / p95 / max seconds, and x real time."""
    group_by = group_by or []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ev in rows:
        groups[(ev["stage"], *[_group_value(ev, g) for g in group_by])].append(ev)
    out = []
    for key, evs in sorted(groups.items(), key=lambda kv: tuple("" if x is None else str(x) for x in kv[0])):
        ok = [e for e in evs if e["status"] == "ok"]
        secs = [e["seconds"] for e in ok]
        timed = [e for e in ok if e["media_seconds"]]
        media = sum(e["media_seconds"] for e in timed)
        spent = sum(e["seconds"] for e in timed)
        row = {"stage": key[0], "group": dict(zip(group_by, key[1:])), "n": len(evs), "errors": len(evs) - len(ok),
               "total_s": round(sum(secs), 3) if secs else None,
               "mean_s": round(statistics.fmean(secs), 3) if secs else None,
               "p50_s": round(_pct(secs, 50), 3) if secs else None,
               "p95_s": round(_pct(secs, 95), 3) if secs else None,
               "min_s": round(min(secs), 3) if secs else None, "max_s": round(max(secs), 3) if secs else None,
               "media_s": round(media, 1) if timed else None,
               "realtime_x": round(media / spent, 2) if timed and spent > 0 else None,
               "mean_concurrent": round(statistics.fmean(e["concurrent"] for e in ok), 2) if ok else None}
        out.append(row)
    return out


def summary(group_by: list[str] | None = None, **filters) -> list[dict]:
    return summarize(all_events(**filters), group_by)


EVENT_COLUMNS = ["id", "ts", "run_id", "stage", "seconds", "media_seconds", "realtime_x", "status", "concurrent",
                 "job_id", "asset_id", "candidate_id", "detail"]


def events_csv(**filters) -> str:
    import json
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EVENT_COLUMNS)
    for e in all_events(**filters):
        w.writerow([json.dumps(e[c], separators=(",", ":")) if c == "detail" else ("" if e[c] is None else e[c])
                    for c in EVENT_COLUMNS])
    return buf.getvalue()
