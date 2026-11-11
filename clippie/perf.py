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


_active: dict[str, dict] = {}


def job_started(job_id: str | None = None, kind: str | None = None, asset_id: str | None = None) -> None:
    global _running
    with _running_lock:
        _running += 1
        if job_id:
            _active[job_id] = {"kind": kind, "asset_id": asset_id, "t0": time.monotonic(),
                               "started_at": datetime.now(timezone.utc)}


def job_finished(job_id: str | None = None) -> None:
    global _running
    with _running_lock:
        _running = max(0, _running - 1)
        if job_id:
            _active.pop(job_id, None)


def jobs_running() -> int:
    return _running


# ---------------------------------------------------------------- recording
def record(stage: str, seconds: float, *, media_seconds: float | None = None, status: str = "ok",
           job_id: str | None = None, asset_id: str | None = None, candidate_id: str | None = None,
           detail: dict | None = None, ts: datetime | None = None) -> None:
    if not get_settings().perf_log:
        return
    ctx = getattr(_ctx, "data", {}) or {}
    try:
        with session() as s:
            s.add(PerfEvent(
                run_id=current_run_id(), stage=stage, seconds=round(float(seconds), 4),
                media_seconds=None if media_seconds is None else round(float(media_seconds), 3), status=status,
                job_id=job_id or ctx.get("job_id"), asset_id=asset_id or ctx.get("asset_id"),
                candidate_id=candidate_id, concurrent=jobs_running(), detail=detail or {},
                **({"ts": ts} if ts else {})))
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
                 "config": r.config, "host": r.host, "current": r.run_id == _run_id,
                 "label": r.label, "notes": r.notes} for r in rows]


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


# ================================================================= dashboard queries
def _utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; everything we store is UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_span(v: str | None, *, minimum: int = 60) -> int:
    """'15m', '1h', '1d' -> seconds."""
    m = _REL.match((v or "").strip())
    if not m:
        raise ValueError(f"cannot read {v!r}: use a bucket size such as 15m, 1h or 1d")
    secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
    if secs < minimum:
        raise ValueError(f"the bucket must be at least {minimum} seconds")
    return secs


def rename_run(run_id: str, label: str | None, notes: str | None) -> bool:
    with session() as s:
        r = s.get(PerfRun, run_id)
        if r is None:
            return False
        if label is not None:
            r.label = label.strip()[:120] or None
        if notes is not None:
            r.notes = notes.strip()[:2000] or None
        s.commit()
        return True


MAX_BUCKETS = 1000


def series(bucket: str = "1h", **filters) -> dict:
    """Time series per stage: for each time bucket, count, errors, p50 / p95 / mean seconds and x real time.
    Only buckets that contain data are returned, so gaps stay gaps on a chart."""
    size = parse_span(bucket)
    rows = all_events(**filters)
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for ev in rows:
        t = _utc(datetime.fromisoformat(ev["ts"])).timestamp()
        groups[(ev["stage"], int(t // size))].append(ev)
    if len({k[1] for k in groups}) > MAX_BUCKETS:
        raise ValueError(f"too many buckets (limit {MAX_BUCKETS}); use a larger bucket or a shorter range")
    stages: dict[str, list[dict]] = defaultdict(list)
    for (stage, b), evs in sorted(groups.items()):
        ok = [e for e in evs if e["status"] == "ok"]
        secs = [e["seconds"] for e in ok]
        timed = [e for e in ok if e["media_seconds"]]
        media, spent = sum(e["media_seconds"] for e in timed), sum(e["seconds"] for e in timed)
        stages[stage].append({
            "t": datetime.fromtimestamp(b * size, timezone.utc).isoformat(), "n": len(evs), "errors": len(evs) - len(ok),
            "p50_s": round(_pct(secs, 50), 3) if secs else None, "p95_s": round(_pct(secs, 95), 3) if secs else None,
            "mean_s": round(statistics.fmean(secs), 3) if secs else None,
            "total_s": round(sum(secs), 3) if secs else None,
            "realtime_x": round(media / spent, 2) if timed and spent > 0 else None})
    return {"bucket_s": size, "stages": dict(stages)}


def _is_job(stage: str) -> bool:
    return stage.startswith("job.")


def _is_work(stage: str) -> bool:
    """Leaf stages: not the job totals (they contain their stages) and not web requests."""
    return not (stage.startswith("job.") or stage.startswith("api."))


def _starts(ev: dict) -> tuple[float, float]:
    end = _utc(datetime.fromisoformat(ev["ts"])).timestamp()
    return end - ev["seconds"], end


def _max_overlap(intervals: list[tuple[float, float]]) -> int:
    marks = sorted([(a, 1) for a, _ in intervals] + [(b, -1) for _, b in intervals], key=lambda m: (m[0], m[1]))
    cur = best = 0
    for _, d in marks:
        cur += d
        best = max(best, cur)
    return best


def _union_length(intervals: list[tuple[float, float]]) -> float:
    """Total time covered by at least one interval (overlaps counted once)."""
    total, cur_s, cur_e = 0.0, None, None
    for a, b in sorted(intervals):
        if cur_e is None or a > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = a, b
        else:
            cur_e = max(cur_e, b)
    return total + (cur_e - cur_s if cur_e is not None else 0.0)


def _bursts(rows: list[dict], gap: float) -> list[dict]:
    """Split one video's events into bursts of activity separated by more than `gap` seconds of silence
    (you add a video today and export it tomorrow: two bursts, not one 24-hour timeline)."""
    evs = sorted(rows, key=lambda e: _starts(e)[0])
    groups: list[list[dict]] = []
    last_end = None
    for e in evs:
        s, en = _starts(e)
        wait = (e["detail"] or {}).get("queue_wait_s") if _is_job(e["stage"]) else None
        s_eff = s - (wait or 0)
        if last_end is None or s_eff - last_end > gap:
            groups.append([])
        groups[-1].append(e)
        last_end = max(last_end or en, en)
    out = []
    for g in groups:
        t0 = min(_starts(e)[0] - (((e["detail"] or {}).get("queue_wait_s") or 0) if _is_job(e["stage"]) else 0) for e in g)
        items, jobs = [], []
        for e in g:
            s, en = _starts(e)
            wait = ((e["detail"] or {}).get("queue_wait_s") or 0) if _is_job(e["stage"]) else 0
            items.append({"stage": e["stage"], "start_s": round(s - t0, 3), "end_s": round(en - t0, 3),
                          "seconds": e["seconds"], "wait_from_s": round(s - wait - t0, 3) if wait else None,
                          "status": e["status"], "job_id": e["job_id"], "candidate_id": e["candidate_id"],
                          "media_seconds": e["media_seconds"], "detail": e["detail"]})
            if _is_job(e["stage"]):
                jobs.append((s, en))
        wall = max(i["end_s"] for i in items)
        out.append({
            "started_at": datetime.fromtimestamp(t0, timezone.utc).isoformat(), "events": items,
            "summary": {"wall_s": round(wall, 3), "busy_s": round(_union_length(jobs), 3),
                        "work_s": round(sum(e["seconds"] for e in g if _is_work(e["stage"])), 3),
                        "queue_wait_s": round(sum(((e["detail"] or {}).get("queue_wait_s") or 0) for e in g if _is_job(e["stage"])), 3),
                        "max_jobs_at_once": _max_overlap(jobs) if jobs else 0, "jobs": len(jobs),
                        "errors": sum(1 for e in g if e["status"] != "ok")}})
    return out


def timeline(asset_id: str, gap: float = 600.0) -> dict:
    rows = all_events(asset_id=asset_id)
    bursts = _bursts(rows, gap)
    return {"asset_id": asset_id, "bursts": bursts,
            "summary": {"bursts": len(bursts), "events": len(rows),
                        "wall_s": round(sum(b["summary"]["wall_s"] for b in bursts), 3),
                        "busy_s": round(sum(b["summary"]["busy_s"] for b in bursts), 3),
                        "work_s": round(sum(b["summary"]["work_s"] for b in bursts), 3),
                        "queue_wait_s": round(sum(b["summary"]["queue_wait_s"] for b in bursts), 3),
                        "max_jobs_at_once": max((b["summary"]["max_jobs_at_once"] for b in bursts), default=0),
                        "errors": sum(b["summary"]["errors"] for b in bursts)}}


def _asset_names(ids: set[str]) -> dict[str, str]:
    from .models import MediaAsset

    if not ids:
        return {}
    with session() as s:
        return {a.id: a.name for a in s.scalars(select(MediaAsset).where(MediaAsset.id.in_(ids))).all()}


def videos(limit: int = 50, offset: int = 0, gap: float = 600.0, **filters) -> list[dict]:
    """One row per video that has measurements: totals, slowest stage, errors. Newest activity first."""
    with session() as s:
        q = _filtered(select(PerfEvent.asset_id, func.max(PerfEvent.ts).label("last")), **filters) \
            .where(PerfEvent.asset_id.is_not(None)).group_by(PerfEvent.asset_id).order_by(func.max(PerfEvent.ts).desc()) \
            .limit(limit).offset(offset)
        ids = [r[0] for r in s.execute(q).all()]
    names = _asset_names(set(ids))
    out = []
    for aid in ids:
        rows = all_events(asset_id=aid)
        bursts = _bursts(rows, gap)
        work: dict[str, float] = defaultdict(float)
        for e in rows:
            if _is_work(e["stage"]) and e["status"] == "ok":
                work[e["stage"]] += e["seconds"]
        slowest = max(work.items(), key=lambda kv: kv[1], default=None)
        probe = next((e for e in rows if e["stage"] == "ingest.probe"), None)
        out.append({"asset_id": aid, "name": names.get(aid), "deleted": aid not in names,
                    "duration_s": probe["media_seconds"] if probe else None,
                    "first_ts": min(_utc(datetime.fromisoformat(e["ts"])).timestamp() - e["seconds"] for e in rows),
                    "last_ts": max(e["ts"] for e in rows), "events": len(rows),
                    "errors": sum(1 for e in rows if e["status"] != "ok"),
                    "bursts": len(bursts), "wall_s": round(sum(b["summary"]["wall_s"] for b in bursts), 2),
                    "busy_s": round(sum(b["summary"]["busy_s"] for b in bursts), 2),
                    "work_s": round(sum(work.values()), 2), "jobs": sum(1 for e in rows if _is_job(e["stage"])),
                    "clips_exported": sum(1 for e in rows if e["stage"] == "export.render" and e["status"] == "ok"),
                    "slowest_stage": {"stage": slowest[0], "total_s": round(slowest[1], 2)} if slowest else None})
    for r in out:   # first_ts as ISO for the API
        r["first_ts"] = datetime.fromtimestamp(r["first_ts"], timezone.utc).isoformat()
    return out


def live(recent: int = 15) -> dict:
    """What is running right now, what is waiting, and what just finished."""
    from .models import Job

    cfg = get_settings()
    now = time.monotonic()
    with _running_lock:
        active = {jid: dict(a) for jid, a in _active.items()}
    with session() as s:
        jobs = {j.id: j for j in s.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all()}
        queued_rows = sorted((j for jid, j in jobs.items() if jid not in active), key=lambda j: j.created_at)
        names = _asset_names({a["asset_id"] for a in active.values() if a["asset_id"]}
                             | {j.asset_id for j in queued_rows if j.asset_id})
        running = []
        for jid, a in sorted(active.items(), key=lambda kv: kv[1]["t0"]):
            j = jobs.get(jid)
            running.append({"job_id": jid, "kind": a["kind"], "asset_id": a["asset_id"], "video": names.get(a["asset_id"]),
                            "stage": j.stage if j else None, "progress": j.progress if j else None,
                            "elapsed_s": round(now - a["t0"], 1)})
        queued = []
        for n, j in enumerate(queued_rows, 1):
            since = _utc(j.updated_at or j.created_at)
            queued.append({"n": n, "job_id": j.id, "kind": j.kind, "asset_id": j.asset_id, "video": names.get(j.asset_id),
                           "waiting_s": round(max(0.0, (datetime.now(timezone.utc) - since).total_seconds()), 1)})
    total = cfg.max_workers
    slots = [{"n": i + 1, "state": "busy", "job_id": r["job_id"], "kind": r["kind"], "video": r["video"],
              "elapsed_s": r["elapsed_s"]} for i, r in enumerate(running[:total])]
    slots += [{"n": i + 1, "state": "idle", "job_id": None, "kind": None, "video": None, "elapsed_s": None}
              for i in range(len(slots), total)]
    finished = events(limit=recent, stage="job.")
    day = parse_when("24h")
    day_rows = all_events(stage="job.", since=day)
    ev_names = _asset_names({e["asset_id"] for e in finished if e["asset_id"]})
    return {"workers": {"total": total, "busy": len(running), "slots": slots}, "running": running, "queued": queued,
            "recent": [{"ts": e["ts"], "kind": e["stage"][4:], "video": ev_names.get(e["asset_id"]), "asset_id": e["asset_id"],
                        "seconds": e["seconds"], "status": e["status"], "realtime_x": e["realtime_x"],
                        "queue_wait_s": (e["detail"] or {}).get("queue_wait_s")} for e in finished],
            "counters": {"running": len(running), "queued": len(queued), "workers_total": total,
                         "finished_24h": sum(1 for e in day_rows if e["status"] == "ok"),
                         "failed_24h": sum(1 for e in day_rows if e["status"] == "error")}}
