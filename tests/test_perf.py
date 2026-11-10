import csv
import io
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clippie import perf
from clippie.api import app
from clippie.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


def tag() -> str:
    """A unique stage prefix per test so tests don't see each other's rows."""
    return "t" + uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------- timer ----------
def test_timer_records_ok_error_and_cancelled():
    t = tag()
    with perf.timer(f"{t}.ok", media_seconds=10, kind="x") as rec:
        time.sleep(0.05)
        rec["detail"]["extra"] = 1
    with pytest.raises(ValueError):
        with perf.timer(f"{t}.err"):
            raise ValueError("boom")

    class Cancelled(Exception):
        pass

    with pytest.raises(Cancelled):
        with perf.timer(f"{t}.cancel"):
            raise Cancelled()

    rows = {e["stage"]: e for e in perf.all_events(stage=t)}
    assert rows[f"{t}.ok"]["status"] == "ok" and rows[f"{t}.ok"]["seconds"] >= 0.05
    assert rows[f"{t}.ok"]["detail"] == {"kind": "x", "extra": 1}
    assert rows[f"{t}.ok"]["realtime_x"] == pytest.approx(10 / rows[f"{t}.ok"]["seconds"], rel=0.01)
    assert rows[f"{t}.err"]["status"] == "error" and rows[f"{t}.cancel"]["status"] == "cancelled"
    assert rows[f"{t}.ok"]["run_id"] == perf.current_run_id()


def test_context_fills_job_and_asset_and_explicit_values_win():
    t = tag()
    perf.set_context(job_id="job-1", asset_id="asset-1")
    try:
        with perf.timer(f"{t}.a"):
            pass
        with perf.timer(f"{t}.b", asset_id="other", candidate_id="cand-9"):
            pass
    finally:
        perf.clear_context()
    with perf.timer(f"{t}.c"):
        pass
    r = {e["stage"]: e for e in perf.all_events(stage=t)}
    assert (r[f"{t}.a"]["job_id"], r[f"{t}.a"]["asset_id"]) == ("job-1", "asset-1")
    assert (r[f"{t}.b"]["asset_id"], r[f"{t}.b"]["candidate_id"]) == ("other", "cand-9")
    assert r[f"{t}.c"]["job_id"] is None                                   # context was cleared


def test_logging_never_breaks_processing(monkeypatch):
    t = tag()

    def broken():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(perf, "session", broken)
    with perf.timer(f"{t}.x"):                                              # no exception escapes
        value = 41 + 1
    assert value == 42


def test_disabled_flag_records_nothing(monkeypatch):
    t = tag()
    monkeypatch.setattr(get_settings(), "perf_log", False)
    with perf.timer(f"{t}.off"):
        pass
    assert perf.all_events(stage=t) == []


# ---------- statistics ----------
def seed(stage, seconds, media=None, status="ok", **detail):
    perf.record(stage, seconds, media_seconds=media, status=status, detail=detail)


def test_summary_statistics_and_realtime_factor():
    t = tag()
    for s in (1, 2, 3, 4, 100):
        seed(f"{t}.render", s, media=20)
    seed(f"{t}.render", 5, media=20, status="error")
    row = perf.summary(stage=t)[0]
    assert row["stage"] == f"{t}.render" and row["n"] == 6 and row["errors"] == 1
    assert row["mean_s"] == pytest.approx(22.0) and row["p50_s"] == 3.0 and row["max_s"] == 100.0 and row["min_s"] == 1.0
    assert row["p95_s"] == pytest.approx(80.8, abs=0.1)                     # linear interpolation, like numpy
    assert row["total_s"] == pytest.approx(110.0)
    assert row["media_s"] == pytest.approx(100.0) and row["realtime_x"] == pytest.approx(100 / 110, abs=0.01)


def test_group_by_detail_key_and_stage_prefix_filter():
    t = tag()
    for s in (10, 12):
        seed(f"{t}.export", s, media=20, kind="vertical")
    seed(f"{t}.export", 4, media=20, kind="original")
    seed(f"{t}.other", 1)
    rows = perf.summary(["detail.kind"], stage=f"{t}.export")
    by = {r["group"]["detail.kind"]: r for r in rows}
    assert by["vertical"]["n"] == 2 and by["vertical"]["mean_s"] == 11.0 and by["original"]["n"] == 1
    assert {r["stage"] for r in perf.summary(stage=t)} == {f"{t}.export", f"{t}.other"}
    assert {r["stage"] for r in perf.summary(stage=f"{t}.exp")} == {f"{t}.export"}       # prefix match
    with pytest.raises(ValueError):
        perf.summary(["nonsense"], stage=t)


def test_time_run_asset_and_status_filters():
    t = tag()
    perf.record(f"{t}.a", 1, asset_id="A1")
    perf.record(f"{t}.a", 2, asset_id="A2", status="error")
    assert len(perf.all_events(stage=t, asset_id="A1")) == 1
    assert len(perf.all_events(stage=t, status="error")) == 1
    assert len(perf.all_events(stage=t, run="current")) == 2
    assert perf.all_events(stage=t, run=str(uuid.uuid4())) == []
    assert len(perf.all_events(stage=t, since=perf.parse_when("1h"))) == 2
    assert perf.all_events(stage=t, since=perf.parse_when("2999-01-01")) == []
    assert perf.parse_when("all") is None and perf.parse_when(None) is None
    with pytest.raises(ValueError):
        perf.parse_when("yesterday-ish")


# ---------- API ----------
def test_api_summary_events_csv_and_runs(client):
    t = tag()
    for s in (2, 4):
        seed(f"{t}.render", s, media=20, kind="vertical")
    r = client.get(f"/api/perf/summary?stage={t}&group_by=detail.kind").json()
    assert r["group_by"] == ["detail.kind"] and r["rows"][0]["n"] == 2 and r["rows"][0]["mean_s"] == 3.0
    ev = client.get(f"/api/perf/events?stage={t}&limit=1").json()["events"]
    assert len(ev) == 1 and ev[0]["stage"] == f"{t}.render"

    csv_resp = client.get(f"/api/perf/events?stage={t}&format=csv")
    assert csv_resp.headers["content-type"].startswith("text/csv") and "attachment" in csv_resp.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(csv_resp.text)))
    assert len(rows) == 2 and set(perf.EVENT_COLUMNS) == set(rows[0])
    assert json.loads(rows[0]["detail"]) == {"kind": "vertical"} and float(rows[0]["realtime_x"]) > 0

    assert client.get("/api/perf/summary?since=banana").status_code == 400
    assert client.get("/api/perf/summary?group_by=bogus").status_code == 400
    assert client.get("/api/perf/events?format=xml").status_code == 400

    runs = client.get("/api/perf/runs").json()["runs"]
    cur = next(r for r in runs if r["current"])
    assert cur["config"]["max_workers"] == get_settings().max_workers and cur["events"] >= 2
    assert cur["host"]["cpu_count"] == os.cpu_count() and "python" in cur["host"]


# ---------- the real pipeline writes the log ----------
@pytest.fixture(scope="module")
def video(tmp_path_factory):
    out = tmp_path_factory.mktemp("perf") / "s.mp4"
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


def asset_of(client, aid):
    return next(a for a in client.get("/api/assets").json() if a["id"] == aid)


def test_pipeline_stages_are_logged_and_survive_deletion(client, video):
    with open(video, "rb") as f:
        aid = client.post("/api/assets/upload", files={"file": ("s.mp4", f, "video/mp4")}).json()["asset_id"]
    until(lambda: asset_of(client, aid)["status"] == "ready")
    client.post(f"/api/assets/{aid}/analyze")
    until(lambda: asset_of(client, aid)["pipeline"]["state"] == "done")
    cid = client.get(f"/api/assets/{aid}/candidates").json()[0]["id"]
    e = client.post(f"/api/candidates/{cid}/export", json={"kinds": ["vertical"]}).json()[0]
    until(lambda: client.get(f"/api/jobs/{e['job_id']}").json()["status"] == "done")
    until(lambda: any(r["stage"] == "export.render" for r in perf.all_events(asset_id=aid)))
    time.sleep(0.5)

    got = {}
    for ev in perf.all_events(asset_id=aid):
        got.setdefault(ev["stage"], []).append(ev)
    for stage in ("ingest.probe", "ingest.thumbnail", "analyze.audio", "analyze.motion", "analyze.candidates",
                  "analyze.save", "preview.render", "export.render", "job.ingest", "job.analyze", "job.preview", "job.export"):
        assert stage in got, f"missing {stage}; have {sorted(got)}"

    probe = got["ingest.probe"][0]
    assert probe["media_seconds"] == pytest.approx(29, abs=1) and probe["detail"]["height"] == 180
    assert got["analyze.audio"][0]["media_seconds"] == pytest.approx(29, abs=1) and got["analyze.motion"][0]["realtime_x"] > 1
    exp = got["export.render"][0]
    assert exp["detail"]["kind"] == "vertical" and exp["detail"]["bytes"] > 0 and exp["detail"]["fps_cap"] == get_settings().vertical_max_fps
    assert exp["candidate_id"] == cid and exp["media_seconds"] > 0
    job = got["job.analyze"][0]
    assert job["status"] == "ok" and job["detail"]["queue_wait_s"] >= 0 and job["media_seconds"] == pytest.approx(29, abs=1)
    assert job["seconds"] >= got["analyze.motion"][0]["seconds"]            # a job takes at least as long as its stages
    assert max(x["concurrent"] for xs in got.values() for x in xs) >= 1

    assert client.delete(f"/api/assets/{aid}").status_code == 200           # metrics outlive the video
    assert any(r["stage"] == "analyze.motion" for r in perf.all_events(asset_id=aid))


def test_failed_job_is_logged_as_error(client, video):
    with open(video, "rb") as f:
        aid = client.post("/api/assets/upload", files={"file": ("s.mp4", f, "video/mp4")}).json()["asset_id"]
    until(lambda: asset_of(client, aid)["status"] == "ready")
    client.patch(f"/api/assets/{aid}", json={"game": "valorant"})           # Valorant needs a player name: the job fails
    j = client.post(f"/api/assets/{aid}/analyze").json()["job_id"]
    until(lambda: client.get(f"/api/jobs/{j}").json()["status"] == "failed")
    time.sleep(0.3)
    jobs = [e for e in perf.all_events(asset_id=aid, stage="job.analyze")]
    assert jobs and jobs[-1]["status"] == "error"


# ---------- command line report ----------
def test_cli_report_prints_a_table_and_writes_csv(tmp_path):
    t = tag()
    for s in (1.0, 3.0):
        seed(f"{t}.render", s, media=10)
    env = {**os.environ, "CLIPPIE_VLM_BASE_URL": ""}
    out = subprocess.run([sys.executable, "-m", "tools.perf_report", "--stage", t], cwd=ROOT, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert f"{t}.render" in out.stdout and "p95" in out.stdout and "x real" in out.stdout.lower()
    path = tmp_path / "events.csv"
    out = subprocess.run([sys.executable, "-m", "tools.perf_report", "--stage", t, "--csv", str(path)], cwd=ROOT, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0 and len(list(csv.DictReader(open(path, encoding="utf-8")))) == 2
    bad = subprocess.run([sys.executable, "-m", "tools.perf_report", "--since", "banana"], cwd=ROOT, env=env,
                         capture_output=True, text=True)
    assert bad.returncode != 0
