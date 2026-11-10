"""Job handlers: import, ingest, analyze, export."""
from __future__ import annotations

import numpy as np

import hashlib
import json

from urllib.parse import urlparse

from . import captions as captions_mod
from . import perf
from . import layout as layout_mod
from . import jobs, media, rendering, vlm
from .detection import ocr
from .profiles import registry
from .config import get_settings
from .db import session
from .detection import candidates as cand
from .detection import signals
from .models import Candidate, Export, GameEvent, MediaAsset
from .storage import get_storage


@jobs.handler("import")
def import_url(job_id, asset_id, params, progress):
    st, cfg = get_storage(), get_settings()
    with session() as s:
        asset = s.get(MediaAsset, asset_id)
        url = asset.source_url
    progress("downloading", 0.0)
    outdir = st.local_path(f"sources/{asset_id}")
    with perf.timer("import.download", host=urlparse(url).hostname) as rec:
        path, title = media.download_url(url, outdir, int(cfg.max_upload_gb * 1024**3),
                                         on_progress=lambda f: progress("downloading", f))
        rec["detail"]["bytes"] = path.stat().st_size
    with session() as s:
        asset = s.get(MediaAsset, asset_id)
        asset.source_key = f"sources/{asset_id}/{path.name}"
        if title and asset.name == asset.source_url[:300]:
            asset.name = title[:300]
        s.commit()
    jobs.enqueue("ingest", asset_id, {"auto_analyze": bool(params.get("auto_analyze"))})


@jobs.handler("ingest")
def ingest(job_id, asset_id, params, progress):
    st, cfg = get_storage(), get_settings()
    with session() as s:
        asset = s.get(MediaAsset, asset_id)
        key = asset.source_key
    progress("probing", 0.1)
    try:
        with perf.timer("ingest.probe") as rec:
            meta = media.probe(st.local_path(key))
            rec["media_seconds"] = meta["duration"]
            rec["detail"].update(width=meta["width"], height=meta["height"], fps=meta["fps"],
                                 size_bytes=meta["size_bytes"], video_codec=meta["video_codec"])
        perf.job_note(media_seconds=meta["duration"])
        if meta["duration"] > cfg.max_duration_hours * 3600:
            raise media.MediaError(f"video longer than {cfg.max_duration_hours}h limit")
        progress("thumbnail", 0.6)
        with perf.timer("ingest.thumbnail"):
            media.thumbnail(st.local_path(key), st.new_path(f"thumbs/{asset_id}.jpg"), min(5.0, meta["duration"] / 2))
    except Exception:
        with session() as s:
            s.get(MediaAsset, asset_id).status = "failed"
            s.commit()
        raise
    with session() as s:
        a = s.get(MediaAsset, asset_id)
        a.meta, a.status = meta, "ready"
        s.commit()
    if params.get("auto_analyze"):
        jobs.enqueue("analyze", asset_id)


@jobs.handler("analyze")
def analyze(job_id, asset_id, params, progress):
    """Checkpointed stages: audio -> video -> candidates. Re-running skips finished stages."""
    st, cfg = get_storage(), get_settings()
    with session() as s:
        asset = s.get(MediaAsset, asset_id)
        if asset.status != "ready":
            raise RuntimeError("asset is not ready")
        src, meta, game = st.local_path(asset.source_key), dict(asset.meta), asset.game
        pcfg = dict(asset.profile_config or {})
    duration = meta["duration"]
    perf.job_note(media_seconds=duration, game=game, width=meta.get("width"), height=meta.get("height"))
    profile = registry.get(game)
    if profile.needs_player_name and not pcfg.get("player_name"):
        raise RuntimeError(f"the {profile.name} profile needs the player's in-game name (set it on the video)")

    audio_ck, video_ck = st.new_path(f"analysis/{asset_id}/audio.npz"), st.new_path(f"analysis/{asset_id}/video.npz")
    force = bool(params.get("force"))

    if force or not audio_ck.exists():
        progress("audio", 0.0)
        with perf.timer("analyze.audio", media_seconds=duration, has_audio=meta.get("has_audio")):
            a = signals.audio_features(src, lambda f: progress("audio", f * 0.3), duration)
        np.savez(audio_ck, **a)
    if force or not video_ck.exists():
        progress("motion", 0.3)
        with perf.timer("analyze.motion", media_seconds=duration, width=meta.get("width"), height=meta.get("height"),
                        fps=meta.get("fps")):
            v = signals.video_features(src, lambda f: progress("motion", 0.3 + f * 0.25), duration)
        np.savez(video_ck, **v)

    game_events = []
    if profile.regions:
        backend = ocr.get_backend()
        frames = {}
        names = list(profile.regions)
        for n, (name, region) in enumerate(profile.regions.items()):
            # Raw OCR is checkpointed per (region geometry, fps, backend); changing the player
            # name or event rules re-derives events without re-running OCR.
            tag = hashlib.sha1(json.dumps([vars(region), profile.ocr_fps, backend.name,
                                           meta["width"], meta["height"]]).encode()).hexdigest()[:10]
            ck = st.new_path(f"analysis/{asset_id}/ocr_{name}_{tag}.json")
            base = 0.55 + 0.35 * n / len(names)
            if force or not ck.exists():
                progress(f"ocr:{name}", base)
                with perf.timer("analyze.ocr", media_seconds=duration, region=name, ocr_fps=profile.ocr_fps,
                                backend=backend.name) as rec:
                    res = ocr.scan_region(src, meta["width"], meta["height"], region, profile.ocr_fps, backend,
                                          on_progress=lambda f, n=n: progress(f"ocr:{names[n]}", base + f * 0.35 / len(names)),
                                          duration=duration)
                    rec["detail"]["frames_with_text"] = len(res)
                ck.write_text(json.dumps(res))
            frames[name] = json.loads(ck.read_text())
        game_events = profile.derive_events(frames, pcfg) if profile.derive_events else []

    progress("candidates", 0.92)
    audio, video = dict(np.load(audio_ck)), dict(np.load(video_ck))
    with perf.timer("analyze.candidates", media_seconds=duration) as rec:
        events, cands = cand.build(
            audio, video, duration,
            pad_before=cfg.candidate_padding_before, pad_after=cfg.candidate_padding_after,
            min_score=cfg.min_candidate_score, max_per_hour=cfg.max_candidates_per_hour,
            game_events=game_events, significance=profile.significance,
        )
        rec["detail"].update(candidates=len(cands), events=len(events) + len(game_events))
    events = list(events) + list(game_events)
    with perf.timer("analyze.save", candidates=len(cands)):
        with session() as s:
            s.query(GameEvent).filter_by(asset_id=asset_id).delete()
            # Replace auto candidates but keep anything the user created or already decided on.
            stale = [c.id for c in s.query(Candidate).filter_by(asset_id=asset_id, manual=False, status="pending").all()]
            s.query(Candidate).filter_by(asset_id=asset_id, manual=False, status="pending").delete()
            for e in events:
                s.add(GameEvent(asset_id=asset_id, game=game, event_type=e.event_type, timestamp=e.timestamp,
                                confidence=e.confidence, source=e.source, evidence=e.evidence))
            for c in cands:
                s.add(Candidate(asset_id=asset_id, start=c.start, end=c.end, peak=c.peak, score=c.score,
                                score_breakdown=c.breakdown, event_types=c.event_types))
            s.commit()
    for cid in stale:  # files belonging to candidates that no longer exist
        st.delete(f"previews/{cid}.mp4")
        st.delete(f"frames/{cid}")
    if cands:
        jobs.enqueue("preview", asset_id)       # trimmed clips first, so they show up quickly
        if captions_mod.available() and (cfg.auto_captions or pcfg.get("auto_captions")):
            jobs.enqueue("captions", asset_id)
    if vlm.enabled() and cfg.auto_verify and cands:
        jobs.enqueue("verify", asset_id)


@jobs.handler("preview")
def preview(job_id, asset_id, params, progress):
    """Render a small trimmed clip per candidate so the UI can play it directly.
    Idempotent: candidates that already have a preview are skipped."""
    st = get_storage()
    with session() as s:
        asset = s.get(MediaAsset, asset_id)
        src, has_audio = st.local_path(asset.source_key), asset.meta.get("has_audio", True)
        src_h = asset.meta.get("height")
        q = s.query(Candidate).filter_by(asset_id=asset_id)
        if params.get("candidate_id"):
            q = q.filter_by(id=params["candidate_id"])
        todo = [(c.id, c.start, c.end) for c in q.order_by(Candidate.start).all() if not c.preview_key]
    for n, (cid, start, end) in enumerate(todo):
        progress("rendering", n / max(1, len(todo)))
        key = f"previews/{cid}.mp4"
        with perf.timer("preview.render", media_seconds=end - start, candidate_id=cid, src_height=src_h) as rec:
            rendering.render(src, st.new_path(key), start, end, "preview", has_audio)
            rec["detail"]["bytes"] = st.local_path(key).stat().st_size
        with session() as s:
            c = s.get(Candidate, cid)
            if c is None or (c.start, c.end) != (start, end):
                st.delete(key)          # deleted or re-trimmed while rendering; a newer job handles it
                continue
            c.preview_key = key
            s.commit()


@jobs.handler("captions")
def captions_job(job_id, asset_id, params, progress):
    """Transcribe the speech of each clip (only that span of audio). Idempotent: clips that already
    have subtitles are skipped unless force=True."""
    st, cfg = get_storage(), get_settings()
    with session() as s:
        asset = s.get(MediaAsset, asset_id)
        src = st.local_path(asset.source_key)
        q = s.query(Candidate).filter_by(asset_id=asset_id)
        if params.get("candidate_id"):
            q = q.filter_by(id=params["candidate_id"])
        todo = [(c.id, c.start, c.end) for c in q.order_by(Candidate.start).all()
                if c.status != "rejected" and (params.get("force") or not c.captions)]
    if not todo:
        return
    progress("loading speech model", 0.0)
    with perf.timer("captions.load_model", model=cfg.whisper_model) as rec:
        captions_mod._load_model()          # first use downloads the model; do it before the per-clip loop
        rec["detail"]["device"] = getattr(captions_mod, "_model_device", None)
    for n, (cid, start, end) in enumerate(todo):
        progress("transcribing", n / len(todo))
        with perf.timer("captions.transcribe", media_seconds=end - start, candidate_id=cid, model=cfg.whisper_model) as rec:
            res = captions_mod.transcribe(src, start, end)
            rec["detail"].update(device=getattr(captions_mod, "_model_device", None), words=len(res["words"]),
                                 language=res["language"], gpu_mem_mb=perf.gpu_mem_used_mb())
        with session() as s:
            c = s.get(Candidate, cid)
            if c is None or (c.start, c.end) != (start, end):
                continue                # deleted or re-trimmed meanwhile; a newer job handles it
            c.captions = {"words": res["words"], "language": res["language"], "model": cfg.whisper_model,
                          "start": start, "end": end}
            s.commit()


@jobs.handler("verify")
def verify(job_id, asset_id, params, progress):
    """Send a few frames per candidate to the VLM. Idempotent: already-verified candidates are
    skipped unless force=True, so a restart resumes where it stopped."""
    st, cfg = get_storage(), get_settings()
    client = vlm.get_client()
    force = bool(params.get("force"))
    with session() as s:
        asset = s.get(MediaAsset, asset_id)
        src = st.local_path(asset.source_key)
        game_name = registry.get(asset.game).name if asset.game != "generic" else "a video game"
        ids = [c.id for c in s.query(Candidate).filter_by(asset_id=asset_id).order_by(Candidate.start).all()
               if c.status != "rejected" and (force or not c.verification)]
    for n, cid in enumerate(ids):
        progress("verifying", n / max(1, len(ids)))
        with session() as s:
            c = s.get(Candidate, cid)
            start, end, peak, types = c.start, c.end, c.peak, list(c.event_types or [])
        frames = []
        with perf.timer("verify.frames", candidate_id=cid, frames=cfg.vlm_frames):
            for i, t in enumerate(vlm.sample_times(start, end, peak, cfg.vlm_frames)):
                out = st.new_path(f"frames/{cid}/{i}.jpg")
                vlm.extract_frame(src, t, out)
                frames.append(out.read_bytes())
        with perf.timer("verify.vlm", candidate_id=cid, frames=len(frames)) as rec:
            result = client.verify(game_name, types, frames)
            rec["detail"].update(model=client.model, verdict=result.get("verdict"))
        with session() as s:
            c = s.get(Candidate, cid)
            bd = dict(c.score_breakdown or {})
            base = bd.setdefault("detector_score", c.score)
            c.score_breakdown = bd
            c.verification = result
            c.score = vlm.fuse_score(base, result)
            s.commit()


@jobs.handler("export")
def export(job_id, asset_id, params, progress):
    st = get_storage()
    export_id = params["export_id"]
    with session() as s:
        ex = s.get(Export, export_id)
        cd = s.get(Candidate, ex.candidate_id)
        asset = s.get(MediaAsset, cd.asset_id)
        src, has_audio = st.local_path(asset.source_key), asset.meta.get("has_audio", True)
        kind, start, end = ex.kind, ex.start, ex.end
        want_subs = bool(ex.subtitles)
        style_d, caps, layout_d = asset.caption_style, cd.captions, asset.layout
        w, h = asset.meta.get("width", 1280), asset.meta.get("height", 720)
        src_fps = asset.meta.get("fps")
        cand_id = cd.id
        ex.status = "running"
        s.commit()
    key = f"exports/{export_id}/{kind}.mp4"
    progress("rendering", 0.1)
    try:
        ass = None
        if want_subs:
            if not caps or (caps.get("start"), caps.get("end")) != (start, end):
                raise RuntimeError("subtitles for this clip are missing or out of date; generate them again")
            out_w, out_h = (1080, 1920) if kind == "vertical" else (w, h)
            ass = captions_mod.build_ass(caps["words"], captions_mod.CaptionStyle.from_dict(style_d), out_w, out_h)
        vf = (layout_mod.vertical_filter(layout_mod.Layout.from_dict(layout_d), w, h, get_settings().vertical_max_fps, src_fps)
              if kind == "vertical" else None)
        lay_mode = layout_mod.Layout.from_dict(layout_d).mode if kind == "vertical" else None
        with perf.timer("export.render", media_seconds=end - start, candidate_id=cand_id, kind=kind,
                        subtitles=want_subs, layout=lay_mode, src_w=w, src_h=h, src_fps=src_fps,
                        fps_cap=get_settings().vertical_max_fps if kind == "vertical" else None,
                        nvenc=get_settings().use_nvenc) as rec:
            rendering.render(src, st.new_path(key), start, end, kind, has_audio, ass_text=ass, vfilter=vf)
            rec["detail"]["bytes"] = st.local_path(key).stat().st_size
    except Exception:
        with session() as s:
            s.get(Export, export_id).status = "failed"
            s.commit()
        raise
    with session() as s:
        ex = s.get(Export, export_id)
        ex.status, ex.key = "done", key
        s.commit()
