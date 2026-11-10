"""Turn a video's jobs into an ordered list of pipeline steps for the UI.

Pure function over plain data so it is easy to test. States: pending | running | done | failed.
"""
from __future__ import annotations

# analyze job progress ranges (see pipeline.analyze): audio 0-.30, motion .30-.55, OCR .55-.90, candidates .90-1
_ANALYZE_RANGES = {"audio": (0.0, 0.30), "motion": (0.30, 0.55), "hud": (0.55, 0.90), "candidates": (0.90, 1.0)}
_ANALYZE_ORDER = ["audio", "motion", "hud", "candidates"]


def _stage_key(stage: str) -> str | None:
    if stage in ("audio", "motion", "candidates"):
        return stage
    if stage.startswith("ocr"):
        return "hud"
    return None


def _latest(jobs: list[dict], kind: str) -> dict | None:
    xs = [j for j in jobs if j["kind"] == kind]
    return xs[-1] if xs else None  # jobs arrive oldest first


def _from_job(job: dict | None, done_if: bool = False) -> tuple[str, float, str | None]:
    if done_if:
        return "done", 1.0, None
    if job is None:
        return "pending", 0.0, None
    if job["status"] == "done":
        return "done", 1.0, None
    if job["status"] == "failed":
        return "failed", job.get("progress") or 0.0, job.get("error")
    if job["status"] in ("running", "queued"):
        return ("running" if job["status"] == "running" else "pending"), job.get("progress") or 0.0, None
    return "pending", 0.0, None


def build_steps(*, asset: dict, jobs: list[dict], has_hud: bool, vlm_on: bool, candidate_count: int,
                verified_count: int, previews_ready: int, captions_wanted: bool = False,
                captions_ready: int = 0) -> dict:
    """asset: {status, has_source_url, has_source}; jobs: oldest first, each {kind,status,stage,progress,error}."""
    steps: list[dict] = []

    def add(id_, label, state, progress=0.0, detail=None):
        steps.append({"id": id_, "label": label, "state": state, "progress": round(progress, 3), "detail": detail})

    if asset["has_source_url"]:
        st, pr, err = _from_job(_latest(jobs, "import"), done_if=asset["has_source"])
        add("download", "Download", st, pr, err)

    ingest = _latest(jobs, "ingest")
    st, pr, err = _from_job(ingest, done_if=asset["status"] == "ready")
    add("prepare", "Prepare video", st, pr, err)

    # ---- analysis stages ----
    analyze = _latest(jobs, "analyze")
    active = [k for k in _ANALYZE_ORDER if k != "hud" or has_hud]
    labels = {"audio": "Audio analysis", "motion": "Motion analysis", "hud": "Game HUD", "candidates": "Find highlights"}
    a_state = analyze["status"] if analyze else ("done" if candidate_count else "none")
    if a_state == "done":
        states = {k: ("done", 1.0, None) for k in active}
    elif analyze and a_state in ("running", "queued", "failed"):
        cur = _stage_key(analyze.get("stage") or "")
        cur_i = active.index(cur) if cur in active else 0
        states = {}
        for i, k in enumerate(active):
            if a_state == "queued" or (a_state == "running" and not analyze.get("stage")):
                states[k] = ("pending", 0.0, None)
            elif i < cur_i:
                states[k] = ("done", 1.0, None)
            elif i == cur_i:
                lo, hi = _ANALYZE_RANGES[k]
                frac = max(0.0, min(1.0, ((analyze.get("progress") or 0.0) - lo) / (hi - lo)))
                if a_state == "failed":
                    states[k] = ("failed", frac, analyze.get("error"))
                else:
                    states[k] = ("running", frac, None)
            else:
                states[k] = ("pending", 0.0, None)
    else:
        states = {k: ("pending", 0.0, None) for k in active}
    for k in active:
        add(k, labels[k], *states[k])

    # While (re)analysing, or after a failed analysis, downstream results are about to be replaced.
    upstream_busy = bool(analyze and analyze["status"] in ("running", "queued", "failed"))

    # ---- AI check ----
    verify = _latest(jobs, "verify")
    if vlm_on or verify or verified_count:
        if upstream_busy:
            add("ai", "AI check", "pending", 0.0)
        elif candidate_count and verified_count >= candidate_count and not (verify and verify["status"] in ("running", "queued")):
            add("ai", "AI check", "done", 1.0)
        else:
            st, pr, err = _from_job(verify)
            if st == "pending" and analyze and analyze["status"] == "done" and vlm_on:
                st = "pending"   # queued behind analysis
            add("ai", "AI check", st, pr, err)

    # ---- trimmed preview clips ----
    preview = _latest(jobs, "preview")
    if upstream_busy:
        add("clips", "Trim clips", "pending", 0.0)
    elif candidate_count and previews_ready >= candidate_count and not (preview and preview["status"] in ("running", "queued")):
        add("clips", "Trim clips", "done", 1.0)
    else:
        st, pr, err = _from_job(preview)
        add("clips", "Trim clips", st, pr, err)

    # ---- subtitles (only when asked for or already generated) ----
    cjob = _latest(jobs, "captions")
    if captions_wanted or cjob or captions_ready:
        if upstream_busy:
            add("captions", "Subtitles", "pending", 0.0)
        elif candidate_count and captions_ready >= candidate_count and not (cjob and cjob["status"] in ("running", "queued")):
            add("captions", "Subtitles", "done", 1.0)
        else:
            st, pr, err = _from_job(cjob)
            add("captions", "Subtitles", st, pr, err)

    done = sum(1 for s in steps if s["state"] == "done")
    running = next((s for s in steps if s["state"] == "running"), None)
    failed = next((s for s in steps if s["state"] == "failed"), None)
    overall = (done + (running["progress"] if running else 0.0)) / len(steps)
    if failed:
        state, label = "failed", f"Failed at {failed['label']}"
    elif done == len(steps):
        state, label = "done", "Complete"
    elif running:
        state, label = "running", f"Step {done + 1} of {len(steps)} · {running['label']}"
    else:
        state, label = "waiting", f"Step {done + 1} of {len(steps)} · waiting"
    return {"steps": steps, "overall": round(overall, 3), "state": state, "label": label}
