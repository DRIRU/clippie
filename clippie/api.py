from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import jobs, media, pipeline, vlm  # noqa: F401  (pipeline registers handlers)
from .config import get_settings
from .db import init_db, session
from .models import Candidate, Export, GameEvent, Job, MediaAsset
from .profiles import registry
from .storage import get_storage

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    get_storage()
    n = jobs.resume_unfinished()
    if n:
        print(f"[clippie] resumed {n} unfinished job(s)")
    yield
    jobs.shutdown()


app = FastAPI(title="clippie (barebone)", lifespan=lifespan)


# ---------- serializers ----------
def _asset(a: MediaAsset) -> dict:
    return {"id": a.id, "name": a.name, "status": a.status, "game": a.game, "meta": a.meta,
            "profile_config": a.profile_config or {},
            "source_url": a.source_url, "created_at": a.created_at.isoformat(),
            "thumb": f"/api/files/thumbs/{a.id}.jpg" if a.status == "ready" else None,
            "video": f"/api/files/{a.source_key}" if a.source_key else None}


def _job(j: Job) -> dict:
    return {"id": j.id, "asset_id": j.asset_id, "kind": j.kind, "status": j.status, "stage": j.stage,
            "progress": j.progress, "error": j.error, "attempts": j.attempts,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None}


def _cand(c: Candidate) -> dict:
    return {"id": c.id, "asset_id": c.asset_id, "start": c.start, "end": c.end, "peak": c.peak,
            "score": c.score, "score_breakdown": c.score_breakdown, "event_types": c.event_types,
            "verification": c.verification,
            "status": c.status, "manual": c.manual}


def _export(e: Export) -> dict:
    return {"id": e.id, "candidate_id": e.candidate_id, "kind": e.kind, "status": e.status,
            "url": f"/api/files/{e.key}" if e.key else None, "start": e.start, "end": e.end}


# ---------- health / profiles ----------
@app.get("/api/health")
def health():
    cfg = get_settings()
    return {"ok": True, "db": cfg.db_url.split(":")[0], "storage": cfg.storage_backend,
            "vlm_enabled": vlm.enabled()}


@app.get("/api/game-profiles")
def game_profiles():
    return [p.public() for p in registry.all_profiles()]


# ---------- assets ----------
@app.post("/api/assets/upload")
def upload(file: UploadFile = File(...), name: str | None = Form(None)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in media.ALLOWED_EXTS:
        raise HTTPException(400, f"unsupported file type {ext!r}")
    cfg, st = get_settings(), get_storage()
    with session() as s:
        a = MediaAsset(name=(name or file.filename or "upload")[:300])
        s.add(a)
        s.commit()
        asset_id = a.id
    key = f"sources/{asset_id}/source{ext}"
    try:
        st.put_stream(key, file.file, max_bytes=int(cfg.max_upload_gb * 1024**3))
    except ValueError as e:
        with session() as s:
            s.query(MediaAsset).filter_by(id=asset_id).delete()
            s.commit()
        raise HTTPException(413, str(e))
    with session() as s:
        s.get(MediaAsset, asset_id).source_key = key
        s.commit()
    job_id = jobs.enqueue("ingest", asset_id)
    return {"asset_id": asset_id, "job_id": job_id}


class ImportBody(BaseModel):
    url: str
    name: str | None = None
    game: str = "generic"
    player_name: str | None = None
    auto_analyze: bool = True


@app.post("/api/assets/import-url")
def import_url(body: ImportBody):
    try:
        media.assert_public_url(body.url)
    except media.MediaError as e:
        raise HTTPException(400, str(e))
    try:
        profile = registry.get(body.game)
    except KeyError:
        raise HTTPException(400, "unknown game profile")
    if body.auto_analyze and profile.needs_player_name and not (body.player_name or "").strip():
        raise HTTPException(400, f"the {profile.name} profile needs your in-game name")
    with session() as s:
        a = MediaAsset(name=(body.name or body.url)[:300], source_url=body.url, game=body.game,
                       profile_config={"player_name": (body.player_name or "").strip()[:64]} if body.player_name else {})
        s.add(a)
        s.commit()
        asset_id = a.id
    return {"asset_id": asset_id, "job_id": jobs.enqueue("import", asset_id, {"auto_analyze": body.auto_analyze})}


@app.get("/api/assets")
def list_assets():
    with session() as s:
        rows = s.query(MediaAsset).order_by(MediaAsset.created_at.desc()).all()
        out = []
        for a in rows:
            d = _asset(a)
            j = s.query(Job).filter_by(asset_id=a.id).order_by(Job.created_at.desc()).first()
            d["latest_job"] = _job(j) if j else None
            d["candidate_count"] = s.query(Candidate).filter_by(asset_id=a.id).count()
            out.append(d)
        return out


def _get_asset(s, asset_id: str) -> MediaAsset:
    a = s.get(MediaAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    return a


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str):
    with session() as s:
        a = _get_asset(s, asset_id)
        jl = s.query(Job).filter_by(asset_id=asset_id).order_by(Job.created_at.desc()).limit(10).all()
        return {**_asset(a), "jobs": [_job(j) for j in jl]}


class AssetPatch(BaseModel):
    game: str | None = None
    player_name: str | None = None


@app.patch("/api/assets/{asset_id}")
def patch_asset(asset_id: str, body: AssetPatch):
    with session() as s:
        a = _get_asset(s, asset_id)
        if body.game is not None:
            try:
                registry.get(body.game)
            except KeyError:
                raise HTTPException(400, "unknown game profile")
            a.game = body.game
        if body.player_name is not None:
            a.profile_config = {**(a.profile_config or {}), "player_name": body.player_name.strip()[:64]}
        s.commit()
        return _asset(a)


class AnalyzeBody(BaseModel):
    force: bool = False


@app.post("/api/assets/{asset_id}/analyze")
def analyze(asset_id: str, body: AnalyzeBody | None = None):
    with session() as s:
        a = _get_asset(s, asset_id)
        if a.status != "ready":
            raise HTTPException(409, "asset is not ready yet")
    return {"job_id": jobs.enqueue("analyze", asset_id, {"force": bool(body and body.force)})}


class VerifyBody(BaseModel):
    force: bool = False


@app.post("/api/assets/{asset_id}/verify")
def verify_asset(asset_id: str, body: VerifyBody | None = None):
    if not vlm.enabled():
        raise HTTPException(409, "VLM verification is not configured (set CLIPPIE_VLM_BASE_URL)")
    with session() as s:
        a = _get_asset(s, asset_id)
        if a.status != "ready":
            raise HTTPException(409, "asset is not ready yet")
    return {"job_id": jobs.enqueue("verify", asset_id, {"force": bool(body and body.force)})}


@app.get("/api/assets/{asset_id}/events")
def events(asset_id: str):
    with session() as s:
        _get_asset(s, asset_id)
        rows = s.query(GameEvent).filter_by(asset_id=asset_id).order_by(GameEvent.timestamp).all()
        return [{"id": e.id, "event_type": e.event_type, "timestamp": e.timestamp, "confidence": e.confidence,
                 "source": e.source, "evidence": e.evidence, "game": e.game} for e in rows]


@app.get("/api/assets/{asset_id}/candidates")
def candidates(asset_id: str):
    with session() as s:
        _get_asset(s, asset_id)
        rows = s.query(Candidate).filter_by(asset_id=asset_id).order_by(Candidate.start).all()
        return [_cand(c) for c in rows]


# ---------- jobs ----------
@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with session() as s:
        j = s.get(Job, job_id)
        if not j:
            raise HTTPException(404, "job not found")
        return _job(j)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    if not jobs.retry(job_id):
        raise HTTPException(409, "only finished or failed jobs can be retried")
    return {"ok": True}


# ---------- candidates ----------
class CandidatePatch(BaseModel):
    start: float | None = None
    end: float | None = None


class CandidateCreate(BaseModel):
    asset_id: str
    start: float
    end: float


def _validate_range(start: float, end: float, duration: float):
    if not (0 <= start < end <= duration + 0.01):
        raise HTTPException(400, f"range must satisfy 0 <= start < end <= {duration:.2f}")


@app.post("/api/candidates")
def create_candidate(body: CandidateCreate):
    with session() as s:
        a = _get_asset(s, body.asset_id)
        _validate_range(body.start, body.end, a.meta.get("duration", 0))
        c = Candidate(asset_id=a.id, start=body.start, end=body.end, peak=body.start, score=0.0,
                      score_breakdown={}, event_types=["manual"], manual=True, status="approved")
        s.add(c)
        s.commit()
        return _cand(c)


def _get_cand(s, cid: str) -> Candidate:
    c = s.get(Candidate, cid)
    if not c:
        raise HTTPException(404, "candidate not found")
    return c


@app.patch("/api/candidates/{cid}")
def patch_candidate(cid: str, body: CandidatePatch):
    with session() as s:
        c = _get_cand(s, cid)
        start = c.start if body.start is None else body.start
        end = c.end if body.end is None else body.end
        _validate_range(start, end, s.get(MediaAsset, c.asset_id).meta.get("duration", 0))
        if (start, end) != (c.start, c.end) and c.verification:
            # verdict was for different frames: drop it and restore the detector score
            bd = dict(c.score_breakdown or {})
            c.score = bd.get("detector_score", c.score)
            c.verification = None
        c.start, c.end = start, end
        s.commit()
        return _cand(c)


def _decide(cid: str, status: str):
    with session() as s:
        c = _get_cand(s, cid)
        c.status = status
        s.commit()
        return _cand(c)


@app.post("/api/candidates/{cid}/approve")
def approve(cid: str):
    return _decide(cid, "approved")


@app.post("/api/candidates/{cid}/reject")
def reject(cid: str):
    return _decide(cid, "rejected")


class ExportBody(BaseModel):
    kinds: list[str] = ["original", "vertical"]


@app.post("/api/candidates/{cid}/export")
def export_candidate(cid: str, body: ExportBody | None = None):
    kinds = (body.kinds if body else None) or ["original", "vertical"]
    if not set(kinds) <= {"original", "vertical"}:
        raise HTTPException(400, "kinds must be original and/or vertical")
    out = []
    with session() as s:
        c = _get_cand(s, cid)
        asset_id, start, end = c.asset_id, c.start, c.end
        exports = [Export(candidate_id=cid, kind=k, start=start, end=end) for k in kinds]
        s.add_all(exports)
        s.commit()
        ids = [(e.id, e.kind) for e in exports]
    for eid, kind in ids:
        jid = jobs.enqueue("export", asset_id, {"export_id": eid})
        out.append({"export_id": eid, "kind": kind, "job_id": jid})
    return out


@app.get("/api/candidates/{cid}/exports")
def candidate_exports(cid: str):
    with session() as s:
        _get_cand(s, cid)
        rows = s.query(Export).filter_by(candidate_id=cid).order_by(Export.created_at.desc()).all()
        return [_export(e) for e in rows]


@app.get("/api/exports/{eid}")
def get_export(eid: str):
    with session() as s:
        e = s.get(Export, eid)
        if not e:
            raise HTTPException(404, "export not found")
        return _export(e)


# ---------- files (barebone local storage; supports Range for seeking) ----------
@app.get("/api/files/{key:path}")
def files(key: str):
    if key.split("/")[0] not in ("sources", "thumbs", "exports"):
        raise HTTPException(404)
    try:
        p = get_storage().local_path(key)
    except ValueError:
        raise HTTPException(404)
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
