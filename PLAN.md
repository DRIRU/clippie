# clippie phased plan

Built from the handoff roadmap, adjusted for `AUDIT.md`. Each phase ends with a check you can run, and nothing moves on until it passes. Durations are estimates and assume one developer.

## Phase 0: Decisions and audit (done / small remainder)
- Done: Qwen runs on the host, repos cloned and pinned, `AUDIT.md` written.
- Remaining (you): confirm the Ultralytics AGPL stance, and get the real model ID from `curl http://127.0.0.1:8000/v1/models`.
- Remaining (you): collect 3-5 sample recordings of your first game (Valorant or CS2), with a few labelled highlight timestamps.

## Cross-cutting: two run modes (applies from Phase 1)

| | Full mode | Barebone mode |
|---|---|---|
| Database | PostgreSQL | SQLite file (WAL on) |
| Object storage | MinIO / S3 | Local filesystem directory |
| Queue | Redis + Celery | Celery with a filesystem broker, or an in-process worker (decide in Phase 1) |
| Compose | all services | `docker-compose.barebone.yml`, or no Docker at all |

Rules that keep both modes working:
- **Database:** `DATABASE_URL` picks the backend. Keep the schema portable: UUIDs as a generic `Uuid` type, a generic `JSON` type (no JSONB-only features), no Postgres-only enums or arrays, and Alembic `render_as_batch` for SQLite. CI runs the migrations and tests against both.
- **Storage:** one `StorageBackend` interface (`put`, `get`, `exists`, `delete`, `presign_get`, `presign_put`, `list`) with `S3Storage` and `LocalStorage` implementations, selected by `STORAGE_BACKEND=s3|local`. Code never touches MinIO or paths directly. Object keys are identical in both modes, so data can be migrated between them.
- **Local uploads and downloads:** there is no presigned URL on a plain directory. `LocalStorage` issues short-lived HMAC-signed URLs that point at API endpoints, which stream the file with range support (so the video player can seek). Uploads are chunked and resumable. This is the one deliberate exception to "never proxy video through FastAPI", and it applies to barebone mode only.
- **MinIO fallback behaviour:** if `STORAGE_BACKEND=s3` is set but the endpoint is unreachable at startup, fail loudly with a clear error. Silent auto-fallback to local disk could split data across two stores. Falling back is an explicit setting.
- **Workers in barebone mode:** SQLite allows one writer, so keep DB writes short, use WAL, and run one CPU and one GPU worker. Concurrency above that needs Postgres.
- `/health` reports which backends are active.

## Phase 1: Skeleton and infrastructure (3-5 days)
Added scope for both modes: the `StorageBackend` interface with both implementations, the portable schema, the barebone compose file, and dual-backend CI.
- Monorepo layout from the handoff, `docker-compose.yml`, `.env.example`.
- Services: postgres, redis, api (FastAPI), worker-cpu, worker-gpu, web (Next.js stub).
- MinIO as an external service via `S3_*` env vars, with the five buckets and a dedicated service account.
- SQLAlchemy + Alembic with the initial tables (users, projects, media_assets, processing_jobs).
- `/health` on api and workers, with checks for DB, Redis, MinIO and the Qwen endpoint.
- Basic auth (single user), since the handoff requires it from the start.
- **Exit check:** `docker compose up` brings every service healthy and the migration runs.

## Phase 2: Ingestion (3-4 days)
- Presigned direct-to-MinIO upload in full mode, so no video passes through FastAPI. Barebone mode uses the signed chunked upload endpoint described above.
- URL import with yt-dlp, with SSRF protection, size and duration limits, and file-type validation.
- FFprobe metadata stored on the asset. Job rows with stage, progress and checkpoints, plus a retry endpoint.
- Frontend: upload/import page and job status page.
- **Exit check:** a 2-hour file uploads and imports, metadata is correct, and killing the worker mid-job then restarting resumes it without a re-download.

## Phase 3: Generic detection (4-6 days)
- Common `GameEvent` schema.
- Detectors written fresh, using Auto-clipper and Chopify as references: audio loudness and spectral flux, motion, scene change, low-FPS frame sampling.
- Candidate generation: merge nearby events, apply padding (8s before, 12s after), and drop overlapping duplicates.
- Evidence stored per event.
- **Exit check:** candidates with timestamps and evidence come out of your sample footage with no Qwen involved. Measure recall against your labelled timestamps.

## Phase 4: First game profile (4-6 days)
- Profile framework and registry, with manual game override. Automatic identification can come after one profile works.
- One game only (Valorant or CS2): kill-feed OCR on configured HUD regions. Benchmark Tesseract vs PaddleOCR here.
- YOLO only if OCR alone isn't enough, and only after the licensing decision.
- **Exit check:** precision and recall on that game's labelled footage, written down. Add a second game only if these numbers are acceptable.

## Phase 5: Qwen verification and scoring (3-4 days)
- Extract selected frames per candidate. Never send whole VODs.
- Targeted prompt through the existing vLLM endpoint, with "unknown" as a valid answer.
- Score fusion with the initial weights, each component stored separately. Detector timestamps stay authoritative for boundaries.
- Runs on the GPU worker with concurrency 1. Record peak VRAM.
- **Exit check:** every candidate has a score breakdown and an explanation.

## Phase 6: Review dashboard (5-8 days)
- Projects, media library, candidate timeline, preview player.
- Accept, reject, trim, extend, merge. Job progress via SSE.
- **Exit check:** the whole flow from upload to approved candidates works without SSH.

## Phase 7: Rendering (3-5 days)
- Original export (stream copy where possible).
- Vertical 1080x1920 with a game-profile crop region, blurred background, and optional facecam layout and captions (faster-whisper, ASS burn-in).
- NVENC with CPU fallback.
- **Exit check:** one approved candidate produces both formats with audio in sync and the HUD preserved.

## Phase 8: Hardening (3-5 days)
- Restart-recovery tests, retention and cleanup, Postgres backups, logging, Nginx and TLS, benchmarks.
- **Exit check:** a worker restart loses no state, and the metrics in handoff section 20 are measured.

## Later (not in the MVP)
Live OBS ingestion (needs the OBS-AI-Highlights license resolved first), more game profiles, social publishing, multi-user.

## Risks to watch
- Game-specific detection quality is the biggest unknown, so Phase 4 gates everything after it.
- The AGPL YOLO question, and vLLM needing to be reachable from the Docker bridge.
- Total estimate is roughly 25-38 working days. Detection quality may push it higher.
