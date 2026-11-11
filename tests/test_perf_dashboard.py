import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from clippie import db, perf
from clippie.api import app
from clippie.config import get_settings
from clippie.db import session
from clippie.models import Job, MediaAsset

UTC = timezone.utc
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def tag() -> str:
    return "d" + uuid.uuid4().hex[:8]


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def seed(stage, seconds, end, *, asset=None, status="ok", media=None, **detail):
    """Record an event that FINISHED `end` seconds after T0 (its start is end - seconds)."""
    perf.record(stage, seconds, media_seconds=media, status=status, asset_id=asset, detail=detail, ts=at(end))


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------- series ----------
def test_series_buckets_statistics_and_gaps(client):
    t = tag()
    for end, sec, st in ((600, 2, "ok"), (2400, 4, "ok"), (3900, 10, "ok"), (4800, 99, "error"), (10800, 1, "ok")):
        seed(f"{t}.render", sec, end, status=st, media=20)
    s = perf.series("1h", stage=t)
    pts = s["stages"][f"{t}.render"]
    assert s["bucket_s"] == 3600 and [p["t"] for p in pts] == [
        "2026-01-01T12:00:00+00:00", "2026-01-01T13:00:00+00:00", "2026-01-01T15:00:00+00:00"]   # 14:00 had no data: a gap
    a, b, c = pts
    assert (a["n"], a["errors"], a["p50_s"], a["mean_s"]) == (2, 0, 3.0, 3.0) and a["p95_s"] == pytest.approx(3.9)
    assert (b["n"], b["errors"], b["p50_s"]) == (2, 1, 10.0)                       # the failed 99 s run is counted, not averaged
    assert (c["n"], c["total_s"]) == (1, 1.0) and a["realtime_x"] == pytest.approx(20 * 2 / 6, abs=0.01)

    hourly = client.get(f"/api/perf/series?bucket=1h&stage={t}").json()
    assert hourly["stages"][f"{t}.render"][0]["n"] == 2
    daily = client.get(f"/api/perf/series?bucket=1d&stage={t}").json()
    assert daily["stages"][f"{t}.render"][0]["n"] == 5 and daily["bucket_s"] == 86400


def test_series_validation(client, monkeypatch):
    assert client.get("/api/perf/series?bucket=banana").status_code == 400
    assert client.get("/api/perf/series?bucket=10s").status_code == 400            # shorter than a minute
    t = tag()
    seed(f"{t}.x", 1, 0)
    seed(f"{t}.x", 1, 30 * 86400)                                                  # a month apart is fine: empty buckets cost nothing
    assert client.get(f"/api/perf/series?bucket=1m&stage={t}").status_code == 200
    for i in range(1, 6):
        seed(f"{t}.y", 1, i * 120)                                                 # five different minutes
    monkeypatch.setattr(perf, "MAX_BUCKETS", 3)                                    # the cap is on how many buckets come back
    r = client.get(f"/api/perf/series?bucket=1m&stage={t}.y")
    assert r.status_code == 400 and "too many buckets" in r.json()["detail"]
    assert client.get("/api/perf/series?since=banana").status_code == 400


# ---------- timeline ----------
def seed_video_story(asset):
    # burst 1: ingest, analyze, two parallel previews, and a web request that must not count as work
    seed("job.ingest", 0.8, 1.0, asset=asset, queue_wait_s=0.2)
    seed("ingest.probe", 0.1, 0.4, asset=asset, media=60)
    seed("job.analyze", 8.0, 10.0, asset=asset, queue_wait_s=0.5)
    seed("analyze.motion", 6.0, 9.0, asset=asset, media=60)
    seed("job.preview", 10.0, 20.0, asset=asset, queue_wait_s=0.0)
    seed("job.preview", 10.0, 21.0, asset=asset, queue_wait_s=0.0)
    seed("preview.render", 9.0, 19.5, asset=asset, media=20)
    seed("api.layout_preview", 0.3, 15.0, asset=asset)
    # burst 2: two hours later
    seed("job.export", 5.0, 7200 + 30, asset=asset, queue_wait_s=1.0)
    seed("export.render", 4.5, 7200 + 29.5, asset=asset, media=20, kind="vertical")


def test_timeline_offsets_waits_bursts_and_summary(client):
    a = "asset-" + tag()
    seed_video_story(a)
    tl = perf.timeline(a)
    assert tl["summary"]["bursts"] == 2 and tl["summary"]["events"] == 10
    b1, b2 = tl["bursts"]
    ev = {e["stage"] + str(i): e for i, e in enumerate(b1["events"])}
    ing = next(e for e in b1["events"] if e["stage"] == "job.ingest")
    assert (ing["start_s"], ing["end_s"], ing["wait_from_s"]) == (0.2, 1.0, 0.0)           # queued from t=0, started at 0.2
    ana = next(e for e in b1["events"] if e["stage"] == "job.analyze")
    assert (ana["start_s"], ana["wait_from_s"]) == (2.0, 1.5)
    assert b1["started_at"] == at(1.0 - 0.8 - 0.2).isoformat()                              # burst starts when the first job queued
    s1 = b1["summary"]
    assert s1["wall_s"] == 21.0 and s1["jobs"] == 4 and s1["max_jobs_at_once"] == 2         # two previews overlapped
    assert s1["busy_s"] == pytest.approx(0.8 + 19.0)                                         # time with a job running: [0.2,1.0] + [2,21]
    assert s1["work_s"] == pytest.approx(0.1 + 6.0 + 9.0)                                    # leaf stages only: no job.* or api.*
    assert s1["queue_wait_s"] == pytest.approx(0.7)
    assert b2["summary"]["wall_s"] == 5.0 + 1.0 and b2["events"][0]["wait_from_s"] == 0.0   # 1 s wait + 5 s job
    assert b2["summary"]["busy_s"] == 5.0                                                    # the wait is not busy time
    assert tl["summary"]["busy_s"] == pytest.approx(19.8 + 5.0)
    assert tl["summary"]["wall_s"] == pytest.approx(21.0 + 6.0)                              # the 2-hour idle gap is not counted
    assert tl["summary"]["queue_wait_s"] == pytest.approx(1.7) and tl["summary"]["max_jobs_at_once"] == 2

    merged = perf.timeline(a, gap=1e9)
    assert merged["summary"]["bursts"] == 1 and merged["bursts"][0]["summary"]["wall_s"] > 7000
    api = client.get(f"/api/perf/timeline?asset_id={a}").json()
    assert api["summary"]["bursts"] == 2 and api["bursts"][0]["events"][0]["detail"] is not None


def test_timeline_empty_and_validation(client):
    r = client.get(f"/api/perf/timeline?asset_id=nope-{tag()}").json()
    assert r["bursts"] == [] and r["summary"]["events"] == 0 and r["summary"]["wall_s"] == 0
    assert client.get("/api/perf/timeline?asset_id=x&gap=0").status_code == 400
    assert client.get("/api/perf/timeline").status_code == 422                               # asset_id is required


# ---------- videos ----------
def make_asset(name):
    with session() as s:
        a = MediaAsset(name=name)
        s.add(a)
        s.commit()
        return a.id


def test_videos_listing_totals_order_and_filters(client):
    a1, a2 = make_asset("Video one " + tag()), make_asset("Video two " + tag())
    gone = "deleted-" + tag()
    try:
        base = (datetime(2100, 1, 1, tzinfo=UTC) - T0).total_seconds()                       # far in the future: newest in any database
        seed("ingest.probe", 0.1, base, asset=a1, media=100)
        seed("export.render", 20.0, base + 50, asset=a1, kind="vertical")
        seed("export.render", 10.0, base + 60, asset=a1, kind="original")
        seed("analyze.motion", 5.0, base + 70, asset=a1, status="error")
        seed("job.export", 31.0, base + 61, asset=a1, queue_wait_s=0)
        seed("analyze.motion", 4.0, base + 100, asset=a2, media=50)
        seed("analyze.audio", 1.0, base + 5000, asset=gone)                                   # newest, and the video no longer exists
        v = {r["asset_id"]: r for r in perf.videos(limit=200)}
        r1, r2, r3 = v[a1], v[a2], v[gone]
        assert r1["name"].startswith("Video one") and not r1["deleted"] and r1["duration_s"] == 100
        assert r1["clips_exported"] == 2 and r1["errors"] == 1 and r1["jobs"] == 1
        assert r1["work_s"] == pytest.approx(0.1 + 20 + 10)                                    # failed stages are not "work done"
        assert r1["slowest_stage"] == {"stage": "export.render", "total_s": 30.0}
        assert r2["name"].startswith("Video two") and r2["slowest_stage"]["stage"] == "analyze.motion"
        assert r3["name"] is None and r3["deleted"] is True                                    # measurements outlive the video
        ordered = [r["asset_id"] for r in perf.videos(limit=200) if r["asset_id"] in (a1, a2, gone)]
        assert ordered == [gone, a2, a1]                                                       # newest activity first
        assert [r["asset_id"] for r in perf.videos(limit=1)] == [gone] and perf.videos(limit=1, offset=1)[0]["asset_id"] == a2
        recent = perf.videos(limit=200, since=at(base + 60))
        assert {a1, a2, gone} >= {r["asset_id"] for r in recent} & {a1, a2, gone}
        api = client.get("/api/perf/videos?limit=500").json()["videos"]
        assert any(r["asset_id"] == a1 for r in api)
        assert client.get("/api/perf/videos?since=banana").status_code == 400
    finally:
        with session() as s:
            s.query(MediaAsset).filter(MediaAsset.id.in_([a1, a2])).delete(synchronize_session=False)
            s.commit()


# ---------- live ----------
def test_live_workers_queue_and_recent(client):
    a = make_asset("Live video " + tag())
    ids = [str(uuid.uuid4()) for _ in range(3)]
    before = perf.live()["workers"]["busy"]
    try:
        with session() as s:
            s.add(Job(id=ids[0], asset_id=a, kind="export", status="running", stage="rendering", progress=0.6))
            s.add(Job(id=ids[1], asset_id=a, kind="analyze", status="running", stage="motion", progress=0.2))
            s.add(Job(id=ids[2], asset_id=a, kind="preview", status="queued"))
            s.commit()
        perf.job_started(ids[0], "export", a)
        perf.job_started(ids[1], "analyze", a)
        live = perf.live()
        total = get_settings().max_workers
        assert live["workers"]["total"] == total and live["workers"]["busy"] == before + 2
        assert len(live["workers"]["slots"]) == total
        mine = {r["job_id"]: r for r in live["running"]}
        assert mine[ids[0]]["stage"] == "rendering" and mine[ids[0]]["progress"] == 0.6 and mine[ids[0]]["video"].startswith("Live video")
        assert mine[ids[0]]["elapsed_s"] >= 0 and mine[ids[1]]["kind"] == "analyze"
        queued = {q["job_id"]: q for q in live["queued"]}
        assert ids[2] in queued and queued[ids[2]]["waiting_s"] >= 0 and queued[ids[2]]["kind"] == "preview"
        assert ids[0] not in queued                                                           # running jobs are not "waiting"
        assert live["counters"]["running"] == before + 2 and live["counters"]["queued"] >= 1
        assert [s["n"] for s in live["workers"]["slots"]] == list(range(1, total + 1))
        assert client.get("/api/perf/live").status_code == 200 and client.get("/api/perf/live?recent=0").status_code == 200
    finally:
        perf.job_finished(ids[0])
        perf.job_finished(ids[1])
        with session() as s:
            s.query(Job).filter(Job.id.in_(ids)).delete(synchronize_session=False)
            s.query(MediaAsset).filter_by(id=a).delete()
            s.commit()
    assert perf.live()["workers"]["busy"] == before                                           # released


def test_live_recent_and_daily_counters():
    before = perf.live()["counters"]
    now = datetime.now(UTC)
    perf.record("job.export", 4.0, status="ok", asset_id="a-" + tag(), detail={"queue_wait_s": 0.3}, ts=now)
    perf.record("job.export", 2.0, status="error", asset_id="a-" + tag(), detail={}, ts=now)
    perf.record("job.export", 2.0, status="ok", asset_id="a-" + tag(), detail={}, ts=now - timedelta(days=3))   # not today
    live = perf.live(recent=5)
    assert live["counters"]["finished_24h"] == before["finished_24h"] + 1
    assert live["counters"]["failed_24h"] == before["failed_24h"] + 1
    top = live["recent"][0]
    assert top["kind"] == "export" and top["status"] in ("ok", "error") and "queue_wait_s" in top


# ---------- run labels, index, shell ----------
def test_run_label_and_notes(client):
    run = perf.current_run_id()
    r = client.patch(f"/api/perf/runs/{run}", json={"label": "  6 workers, vertical 30 fps  ", "notes": "after the blur change"})
    assert r.status_code == 200 and r.json()["label"] == "6 workers, vertical 30 fps" and r.json()["notes"] == "after the blur change"
    got = next(x for x in client.get("/api/perf/runs").json()["runs"] if x["run_id"] == run)
    assert got["label"] == "6 workers, vertical 30 fps"
    assert client.patch(f"/api/perf/runs/{run}", json={"label": ""}).json()["label"] is None          # empty clears it
    assert client.patch(f"/api/perf/runs/{run}", json={"label": "x" * 500}).json()["label"] == "x" * 120
    assert client.patch(f"/api/perf/runs/{uuid.uuid4()}", json={"label": "x"}).status_code == 404
    client.patch(f"/api/perf/runs/{run}", json={"label": "", "notes": ""})


def test_composite_index_exists():
    if not str(db._engine.url).startswith("sqlite"):
        pytest.skip("index check written for SQLite")
    with db._engine.connect() as c:
        names = {r[0] for r in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_perf_events_stage_ts" in names


def test_shell_files_are_served(client):
    assert "Dash.mount('stages')" in client.get("/perf.html").text
    js = client.get("/dash.js")
    assert js.status_code == 200 and "window.Dash" in js.text and "id: 'stages'" in js.text
    assert client.get("/dash.css").status_code == 200


# ---------- the real pipeline feeds the dashboard queries ----------
@pytest.fixture(scope="module")
def video(tmp_path_factory):
    out = tmp_path_factory.mktemp("dash") / "s.mp4"
    fc = ("color=c=gray:s=320x180:r=30:d=12[v1];testsrc2=s=320x180:r=30:d=5[v2];color=c=gray:s=320x180:r=30:d=12[v3];"
          "[v1][v2][v3]concat=n=3:v=1:a=0[v];sine=f=220:d=12,volume=0.02[a1];anoisesrc=d=5:a=0.8:c=white[a2];"
          "sine=f=220:d=12,volume=0.02[a3];[a1][a2][a3]concat=n=3:v=0:a=1[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def until(fn, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    raise TimeoutError


def test_real_video_appears_in_timeline_videos_and_live(client, video):
    with open(video, "rb") as f:
        aid = client.post("/api/assets/upload", files={"file": ("dash.mp4", f, "video/mp4")}).json()["asset_id"]
    until(lambda: next(a for a in client.get("/api/assets").json() if a["id"] == aid)["status"] == "ready")
    client.post(f"/api/assets/{aid}/analyze")
    until(lambda: next(a for a in client.get("/api/assets").json() if a["id"] == aid)["pipeline"]["state"] == "done")
    until(lambda: any(e["stage"] == "job.preview" for e in perf.all_events(asset_id=aid)))
    time.sleep(0.5)

    tl = client.get(f"/api/perf/timeline?asset_id={aid}").json()
    assert tl["summary"]["bursts"] == 1
    stages = [e["stage"] for e in tl["bursts"][0]["events"]]
    for s in ("job.ingest", "ingest.probe", "job.analyze", "analyze.motion", "job.preview", "preview.render"):
        assert s in stages, stages
    starts = [e["start_s"] for e in tl["bursts"][0]["events"]]
    assert starts == sorted(starts) and min(starts) >= 0                                      # laid out in time, never before 0
    motion = next(e for e in tl["bursts"][0]["events"] if e["stage"] == "analyze.motion")
    analyze_job = next(e for e in tl["bursts"][0]["events"] if e["stage"] == "job.analyze")
    assert analyze_job["start_s"] <= motion["start_s"] + 0.05 and motion["end_s"] <= analyze_job["end_s"] + 0.05   # a stage sits inside its job
    assert tl["summary"]["wall_s"] >= motion["seconds"] and tl["summary"]["work_s"] > 0

    vids = client.get("/api/perf/videos?limit=200").json()["videos"]
    mine = next(r for r in vids if r["asset_id"] == aid)
    assert mine["name"] == "dash.mp4" and mine["duration_s"] == pytest.approx(29, abs=1) and mine["jobs"] >= 3
    assert mine["slowest_stage"]["stage"] and mine["errors"] == 0

    live = client.get("/api/perf/live").json()
    assert aid not in [r["asset_id"] for r in live["running"]]                                  # everything has finished
    assert any(r["kind"] == "preview" for r in live["recent"]) or live["counters"]["finished_24h"] >= 3
