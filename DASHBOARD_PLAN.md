# Performance dashboard: plan

## Status

| Phase | State |
|---|---|
| D0 Design | **Done.** Four boards (Overview, Compare, Video timeline, Live) designed in Claude Design, using sample data. |
| D1 Foundation | **Done.** Shared shell, `series`, `timeline`, `videos` and `live` endpoints, run labels, composite index, 22 tests. |
| D2 Overview and Stages | Next |
| D3 to D8 | Not started |

D1 notes: the timeline groups a video's activity into **bursts** (activity separated by more than 10 minutes of silence), so a video added today and exported tomorrow is two bursts rather than one 24-hour bar. Each burst reports wall-clock time, **active time** (time with at least one job running), summed work time, queue wait, and the most jobs at once. The `live` endpoint reads running jobs from an in-memory registry of the job runner, queued jobs from the database, and recent results from the performance log. The dashboard tabs that are not built yet show as "soon" in the shared header.

**Goal:** turn the performance log from a table into a dashboard that answers questions at a glance, shows trends, compares settings, explains why one video was slow, and shows what is running right now.

**Scope assumed here:** the performance and job data. The same shell can later host other pages (library statistics, disk usage), but they are out of scope.

## 1. What exists today

| Piece | State |
|---|---|
| Data | `perf_events` (stage, seconds, media seconds, status, concurrency, detail) and `perf_runs` (settings and hardware per server start). Indexed by stage, time, run and video. No URLs are stored, only the host. |
| API | `/api/perf/summary`, `/events` (JSON or CSV), `/runs` |
| UI | `/perf.html`: filters and one summary table. No charts, no trends, no drill-down. |
| CLI | `python -m tools.perf_report` |
| Live job state | in the `processing_jobs` table (status, stage, progress) and `perf.jobs_running()` |

A stage's start time can already be derived (`ts` is when it finished, so start = `ts - seconds`), so a timeline view needs no schema change.

## 2. Questions the dashboard must answer

1. **Right now:** what is running, what is queued, how many workers are busy, is anything stuck?
2. **Where does the time go?** Which stage dominates a typical video?
3. **Is it getting slower or faster?** Trend of a stage over days.
4. **Did that change help?** Workers 2 versus 6, NVENC on or off, a new filter, a different model.
5. **Why was this video slow?** A per-video timeline of its jobs and stages.
6. **How long will this take?** An estimate for a new video from past speed.
7. **Is something failing?** Error rate per stage and which videos failed.

## 3. Screens

```
 Overview | Stages | Compare | Videos | Live | Resources
```

**Overview** (the landing page)
- KPI cards: videos processed, video hours processed, clips exported, average x real time, error rate, median queue wait (each for the chosen time range, with change versus the previous period).
- Chart: "Where the time goes", a stacked bar per stage as a share of total time.
- Chart: daily trend of the three slowest stages (p50 line, p95 band).
- A short "needs attention" list: stages with rising error rates or p95 more than 2x their baseline.

**Stages**
- Pick a stage: distribution (histogram), trend over time, x real time versus video length (scatter, to spot non-linear behaviour), breakdown by `detail` (export format, layout, device, resolution).
- The existing table stays here, with sortable columns and a CSV button.

**Compare**
- Pick two runs (or one run versus all history) and see side-by-side p50 and p95 per stage with the percentage difference, coloured only when the change is larger than the noise.
- Warns when runs are not comparable (different concurrency, different video lengths).
- Runs can be given a label ("6 workers, no NVENC").

**Videos**
- List of videos with total processing time, slowest stage, errors.
- Click through to a **timeline (waterfall)**: one bar per stage laid out in time, grouped by job, showing waits between jobs (queue time) and parallel work.

**Live**
- Running jobs with progress, elapsed time and current stage; queue depth; worker utilisation (busy of N); recent completions and failures. Updates every 2 seconds.

**Resources** (phase 2, see section 5)
- CPU, memory and GPU utilisation over time, overlaid on the timeline, to show whether a stage is CPU-bound, GPU-bound or waiting.

## 4. API additions

| Endpoint | Purpose |
|---|---|
| `GET /api/perf/series?stage=&bucket=1h\|1d&agg=p50,p95,count,errors` | time series per bucket for the trend charts |
| `GET /api/perf/compare?a=<run>&b=<run>&stage=` | per-stage statistics for two runs, with differences and comparability warnings |
| `GET /api/perf/timeline?asset_id=` | all events of one video with start offsets, for the waterfall |
| `GET /api/perf/live` | running and queued jobs, workers busy, recent finished jobs |
| `GET /api/perf/estimate?duration=&game=&format=` | expected processing time from historical x real time (with a range) |
| `GET /api/perf/videos` | per-video totals for the Videos screen |
| `PATCH /api/perf/runs/{id}` | set a run's label and notes |

Schema changes are small: a `label` and `notes` on `perf_runs`, a composite index on `(stage, ts)`, and later a `perf_samples` table for resource sampling.

## 5. Resource sampling (optional second slice)

- A background thread samples CPU %, memory, and GPU utilisation and memory every 2 seconds into `perf_samples` (cheap rows, pruned after 14 days).
- This needs one new dependency (`psutil`) plus `nvidia-smi` for the GPU, which we already call.
- It turns "export took 21 s at concurrency 6" into "CPU was pegged at 100%", which is the proof needed for deciding the worker count.

## 6. Technology and design

- **No build step, no framework**, consistent with the rest of the UI: static HTML and vanilla JavaScript in `clippie/static/`, one shared header and navigation.
- **Charts:** small hand-written SVG helpers (bar, line with p95 band, stacked bar, histogram, waterfall), roughly 300 lines, no dependency, works offline. If that grows past that, vendor a single small library (uPlot) rather than loading from a CDN, to keep the app fully local.
- **Design first:** make the boards in Claude Design (Overview, Compare, Waterfall, Live) and review them before building, as we did for the main UI. Reuse the existing dark tokens, spacing and components.
- **Chart rules:** colour-blind-safe palette (not red/green alone), every chart has a data table or tooltip with exact values, units on every axis, sensible empty states ("no data yet; process a video"), and reduced-motion respected.
- **Updates:** polling every 2 s on the Live screen and on demand elsewhere. Server-sent events are possible later but not needed.

## 7. Metrics and how they are defined

| Metric | Definition |
|---|---|
| x real time | seconds of video processed per second of work (higher is faster) |
| p50 / p95 | median and 95th percentile of successful timings |
| Throughput | video hours processed per wall-clock hour, clips exported per hour |
| Queue wait | time a job spent queued before starting |
| Error rate | failed or cancelled divided by all timings, per stage |
| Contention | average concurrent jobs while the stage ran; shown next to every comparison |

## 8. Phases

| Phase | Work | Estimate | Done when |
|---|---|---|---|
| D0 Design | Claude Design boards for Overview, Compare, Waterfall, Live; review and adjust | 0.5 day | you approve the look |
| D1 Foundation | shared nav and shell, series, timeline, live and videos endpoints, composite index, API tests | 1 day | endpoints tested on seeded data |
| D2 Overview and Stages | KPI cards, "where the time goes", trend charts, stage detail | 1.5 days | answers questions 2, 3 and 7 |
| D3 Compare | run labels, compare endpoint and screen, comparability warnings | 1 day | answers question 4 |
| D4 Videos and timeline | video list, waterfall, links from the main library | 1 day | answers question 5 |
| D5 Live | running and queued jobs, worker use, recent results | 0.5 day | answers question 1 |
| D6 Resources | sampler, `perf_samples`, overlay on timeline | 1 day | CPU/GPU visible during a run |
| D7 Estimates and benchmark | estimate endpoint and "expected time" in the import box; a `tools.benchmark` command that runs a fixed workload alone and stores it as a labelled run | 1 to 1.5 days | question 6 answered; repeatable baselines |
| D8 Hardening | retention and roll-ups for old events, query timing, docs | 0.5 day | large logs stay fast |

**MVP is D0 to D4, about 5 days.** D5 to D8 can follow in any order.

## 9. Risks and how we handle them

- **Noisy comparisons.** Timings under load include contention. The compare screen shows concurrency and warns; D7 adds a benchmark that runs alone for clean baselines.
- **Synthetic versus real footage.** Speeds on test video differ from gameplay. The dashboard labels all numbers as measured on your machine and your footage, and nothing is hard-coded as a claim.
- **Log growth.** About 30 rows per processed video is small, but queries should stay fast at millions of rows: composite index now, roll-ups later (D8).
- **No authentication yet.** The dashboard exposes timing data and video identifiers, so it stays on `127.0.0.1` like the rest of the app. Authentication remains on the main roadmap.
- **Scope creep.** Charts for everything is tempting; each screen is tied to one of the seven questions above, and nothing ships without one.

## 10. Testing

- API tests on seeded events for every new endpoint (bucketing, percentiles, comparisons, timeline offsets, estimates).
- Browser checks of each screen with real data and with empty data.
- A test that charts and tables agree (same numbers from the same endpoint).
- Accessibility pass: keyboard focus, labels, contrast, data tables behind charts.

## 11. Decisions needed from you

1. **Scope:** performance dashboard only (recommended), or also library and storage statistics?
2. **Charts:** hand-written SVG with no dependency (recommended), or one vendored charting library?
3. **Resource sampling:** include it (needs `psutil`), or skip it for now?
4. **Order:** start with D0 and D1 (design and foundation), then D2 to D4 for the MVP?
