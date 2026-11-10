# FRAGFORGE — COMPLETE PROJECT HANDOFF & IMPLEMENTATION SPECIFICATION

**Document purpose:** Transfer the complete project context, technical decisions, architecture and implementation plan to another AI coding/research agent.

**Project name:** FragForge  
**Project category:** Self-hosted AI gaming highlight detection and video clipping platform  
**Deployment target:** User's own Linux server with NVIDIA L40S GPU  
**Initial operating mode:** VOD-only  
**Game support:** Multiple games, automatic detection with manual override  
**Output formats:** Original aspect ratio + vertical 9:16  
**Development approach:** Open-source integration + custom modular implementation

---

# 1. PROJECT OVERVIEW

FragForge is a personal, self-hosted AI gaming content creation platform inspired by commercial AI clipping tools such as Kixy.

The objective is to automatically analyse gaming recordings and long-form gaming streams, identify interesting moments, generate highlight clips, and export them in formats suitable for YouTube, TikTok, Instagram Reels and other platforms.

The system must support:

1. Locally recorded gameplay videos.
2. Public Twitch VODs.
3. Public YouTube gaming videos and VODs.
4. Multiple games.
5. Automatic game identification.
6. Manual game selection and override.
7. Automatic identification of interesting gameplay events.
8. Candidate clip generation and ranking.
9. AI-assisted verification.
10. Manual candidate review and editing.
11. Original-resolution video export.
12. Vertical 9:16 video export.
13. Optional subtitles and facecam layouts.
14. Persistent processing jobs with retry and recovery.

The project is intended for personal use initially, but the architecture should allow future multi-user functionality.

The platform should be local-first and use open-source components wherever practical.

It should not depend on paid AI APIs for its primary detection pipeline.

# 2. FINALIZED PRODUCT CONFIGURATION

These decisions have already been made. Do not ask the user to choose them again.

| Configuration | Final value |
|---|---|
| Live clipping | VOD only |
| Game identification | Automatic + manual override |
| Video input | Local recordings + Twitch/YouTube VODs |
| Game support | Multiple games |
| Output | Original + vertical 9:16 |
| Deployment | Self-hosted |
| Main GPU | NVIDIA L40S |
| Vision model | Existing Qwen2.5-VL-7B-AWQ vLLM service |
| Frontend | Next.js |
| Backend | FastAPI |
| Job queue | Redis + Celery |
| Database | PostgreSQL |
| Object storage | MinIO |
| Media processing | FFmpeg |
| Initial AI processing | Local GPU and CPU workers |

Important: VOD-only means live OBS/replay-buffer clipping is explicitly outside the initial release. The architecture should allow it to be added later without redesigning the processing pipeline.

# 3. USER'S EXISTING TECHNICAL INFRASTRUCTURE

The user already has an NVIDIA L40S GPU server.

Known GPU details:

- NVIDIA L40S.
- Approximately 46 GiB usable VRAM.
- Existing vLLM deployment.
- Existing Qwen2.5-VL-7B-AWQ model.
- Existing vLLM API on port 8000.
- The user has other NVIDIA GPU systems, including an RTX 5060 and RTX 3090, but the primary target for FragForge is the L40S.

The existing Qwen service should be reused.

Do not automatically deploy another copy of Qwen2.5-VL. FragForge should call the existing OpenAI-compatible vLLM API.

Before configuring the model, verify its exposed model ID:

```bash
curl http://127.0.0.1:8000/v1/models
```

The actual model identifier must be read from that response rather than guessed.

The Qwen endpoint may be running either:

- Directly on the Linux host.
- Inside a Docker container.

This is still an unresolved deployment detail. Ask the user whether the existing Qwen deployment runs in Docker or directly on the host before finalising the Docker networking configuration.

The user's broader technical background includes Python, Django, FastAPI, PostgreSQL, Redis, Celery, Docker, MinIO, FFmpeg, Linux server administration and AI inference infrastructure.

# 4. REPOSITORIES RESEARCHED

The following repositories were identified as useful open-source starting points.

The project should not simply deploy all these applications independently. The intention is to reuse proven algorithms, modules and workflow concepts within a new unified FragForge codebase.

## 4.1 Auto-clipper

Repository:

https://github.com/bendawg2010/Auto-clipper

Purpose:

Primary reference for gaming-specific detection.

Documented features include:

- Twitch and YouTube VOD processing.
- Local video processing.
- YOLO-based visual detection.
- Pixel and computer-vision analysis.
- Audio/voice-triggered clipping.
- Hybrid detection.
- Game-specific profiles.
- Custom game profile support.

Documented profiles include Arc Raiders and War Thunder.

Proposed use:

- Adapt the game-profile concept.
- Reuse relevant CV/YOLO detection logic.
- Study the timestamp/event detection architecture.
- Reuse suitable ingestion or clip-boundary logic.
- Adapt the hybrid detection approach.

Do not assume every advertised game has a fully mature detection profile.

Repository license was reported as MIT during initial research. Verify the current license and dependency licenses before copying code.

## 4.2 Chopify

Repository:

https://github.com/mehbul/chopify

Purpose:

Primary reference for the VOD-processing workflow and candidate review.

Documented capabilities include:

- Local video uploads.
- YouTube and other supported URL imports.
- yt-dlp-based downloading.
- Long-video processing.
- Resumable processing.
- Audio-first processing.
- Batch jobs.
- Caption generation.
- Editable timestamps.
- Semantic search.
- Visual and audio-based discovery.
- Action-only and silent gameplay handling.
- Facecam handling.
- MP4/SRT/poster/metadata exports.

Its documented visual baseline uses low-resolution frame sampling at approximately 2 FPS and 160x90 pixels. This is useful for coarse discovery, but should not be treated as sufficient for identifying semantically meaningful gaming events such as kills, aces or clutches.

The existing Chopify dashboard is intended for a single trusted local user. It should not be exposed publicly as FragForge's primary dashboard without appropriate authentication and security work.

Proposed use:

- Adapt long-VOD processing concepts.
- Adapt resumable workflows.
- Study audio-first analysis.
- Reuse suitable captioning and export patterns.
- Study candidate timestamps and review workflows.
- Use it as a reference for FFmpeg operations.

Do not import its entire application into FragForge.

## 4.3 Game Highlight Detector

Repository:

https://github.com/rfypych/video-highlight-detection

Purpose:

Gaming event detection reference.

Relevant capabilities and concepts:

- Audio analysis.
- OCR.
- Kill-feed and death-message detection.
- Low-health detection.
- Clutch-related visual cues.
- Highlight extraction.

Its original focus is Minecraft PvP.

Potential adaptations:

- Valorant kill-feed detection.
- CS2 kill-feed detection.
- PUBG knockdown and elimination detection.
- Game-specific HUD detection.
- Audio-event detection.

The existing game-specific assumptions must not be treated as universal.

## 4.4 OBS-AI-Highlights

Repository:

https://github.com/VicelikeElm/OBS-AI-Highlights

Purpose:

Future live-mode reference.

Relevant concepts:

- OBS replay buffer.
- OBS WebSocket integration.
- Whisper.
- OCR.
- Game-event detection.
- Configurable game profiles.
- Vertical highlight output.

This is not part of the initial VOD-only implementation.

It may be integrated later as a live ingestion adapter that sends captured segments into the existing FragForge pipeline.

## 4.5 Repository integration strategy

Recommended mapping:

| FragForge functionality | Repository/reference |
|---|---|
| Gaming-specific profile design | Auto-clipper |
| YOLO and CV detection | Auto-clipper |
| Long VOD processing | Chopify |
| Resumable processing | Chopify |
| Caption workflow | Chopify |
| Candidate review concepts | Chopify |
| Kill-feed OCR | Game Highlight Detector |
| Audio-event concepts | Game Highlight Detector |
| Future live ingestion | OBS-AI-Highlights |
| Core application | Custom FragForge code |

Before implementation, conduct a real repository audit.

Do not assume that README documentation proves that the underlying functionality works as advertised.

The next agent should:

1. Clone the repositories into a separate audit directory.
2. Pin their current commits.
3. Inspect source files and project structure.
4. Inspect dependency versions.
5. Inspect tests.
6. Inspect model requirements and weights.
7. Check individual licenses.
8. Identify reusable functions and classes.
9. Run a basic local test where possible.
10. Document what can be integrated and what needs rewriting.

Avoid copying entire standalone applications into FragForge.

---

# 5. TARGET SYSTEM ARCHITECTURE

FragForge should use a modular monorepo.

## 5.1 Frontend

Technology:

- Next.js.
- React.
- TypeScript.
- Video preview component.
- Timeline interface.
- Candidate management interface.

Main pages:

1. Dashboard.
2. Projects.
3. Media library.
4. Upload/import page.
5. Processing status.
6. Game profile selection.
7. Candidate timeline.
8. Candidate preview and editor.
9. Export configuration.
10. Export history.
11. Settings.

Main dashboard should show:

- Total projects.
- Total processed video hours.
- Processing jobs.
- Generated candidates.
- Approved clips.
- Failed jobs.
- Recent exports.

## 5.2 Backend

Technology:

- FastAPI.
- Python.
- SQLAlchemy.
- Alembic.
- PostgreSQL.
- Redis.
- Celery.

Responsibilities:

- Authentication.
- Project management.
- Media asset management.
- Upload URL generation.
- VOD import requests.
- Job orchestration.
- Game profile configuration.
- Candidate management.
- Export requests.
- Processing status.
- Database persistence.

The API must not perform expensive video processing synchronously.

It should enqueue jobs and return job identifiers.

## 5.3 Worker architecture

Separate CPU and GPU workers.

### CPU worker

Responsibilities:

- yt-dlp downloading.
- FFprobe.
- FFmpeg audio extraction.
- Video metadata extraction.
- Thumbnail generation.
- Frame sampling.
- OpenCV motion analysis.
- Audio feature extraction.
- OCR where CPU execution is appropriate.
- Clip rendering.
- Video encoding.
- Cleanup.

### GPU worker

Responsibilities:

- YOLO inference.
- Selected-frame visual analysis.
- Candidate verification.
- Qwen2.5-VL API calls.
- Other GPU-intensive models.

Qwen inference is provided by the existing external vLLM service.

Do not load another Qwen model into the FragForge GPU worker.

Initially configure one concurrent GPU job.

## 5.4 Storage

Use:

- PostgreSQL for metadata.
- MinIO for video and image objects.
- Redis for queues and coordination.
- Local temporary storage for intermediate processing.

Recommended MinIO buckets:

```text
fragforge-sources
fragforge-proxies
fragforge-frames
fragforge-candidates
fragforge-exports
```

Use private buckets and short-lived presigned URLs.

Use a dedicated MinIO service account.

Do not reuse production CCTV database credentials or existing VProtect storage credentials.

---

# 6. RECOMMENDED MONOREPO STRUCTURE

```text
fragforge/
│
├── apps/
│   └── web/
│       ├── app/
│       ├── components/
│       ├── hooks/
│       ├── lib/
│       └── Dockerfile
│
├── services/
│   ├── api/
│   │   ├── app/
│   │   │   ├── routes/
│   │   │   ├── schemas/
│   │   │   ├── models/
│   │   │   ├── services/
│   │   │   └── main.py
│   │   └── Dockerfile
│   │
│   ├── worker-cpu/
│   │   ├── tasks/
│   │   ├── processors/
│   │   └── Dockerfile
│   │
│   └── worker-gpu/
│       ├── tasks/
│       ├── models/
│       ├── inference/
│       └── Dockerfile
│
├── packages/
│   ├── media/
│   │   ├── ffmpeg.py
│   │   ├── ffprobe.py
│   │   ├── sampling.py
│   │   └── extraction.py
│   │
│   ├── detection/
│   │   ├── base.py
│   │   ├── motion.py
│   │   ├── audio.py
│   │   ├── ocr.py
│   │   ├── yolo.py
│   │   └── scene.py
│   │
│   ├── game_profiles/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── valorant.py
│   │   ├── cs2.py
│   │   ├── pubg.py
│   │   └── gta.py
│   │
│   ├── scoring/
│   │   ├── fusion.py
│   │   └── ranking.py
│   │
│   └── rendering/
│       ├── original.py
│       ├── vertical.py
│       ├── captions.py
│       └── templates.py
│
├── migrations/
├── tests/
├── infra/
│   ├── docker/
│   ├── nginx/
│   └── scripts/
│
├── docker-compose.yml
├── .env.example
└── README.md
```

---

# 7. VIDEO PROCESSING PIPELINE

The pipeline must work for both local recordings and imported VODs.

## Stage 1: Ingestion

Input types:

- MP4.
- MKV.
- MOV.
- WebM.
- Public Twitch VOD URL.
- Public YouTube video/VOD URL.

Use yt-dlp for supported URL downloads.

Use FFprobe to extract:

- Duration.
- Width.
- Height.
- FPS.
- Codec.
- Audio streams.
- Video streams.
- Frame rate/time base.

Store the source in MinIO.

Create a media asset record in PostgreSQL.

Do not proxy multi-gigabyte video uploads through FastAPI. Use direct-to-MinIO presigned upload URLs.

## Stage 2: Game identification

Game identification should be automatic but allow manual override.

Possible signals:

- HUD layout.
- Minimap.
- Health bar.
- Weapon UI.
- Kill feed.
- Menu/UI elements.
- Game-specific visual signatures.
- Project name and filename hints.

Use sampled frames rather than processing the full video at high FPS.

The game identifier should return:

```json
{
  "game": "valorant",
  "confidence": 0.94,
  "method": "visual_hud_detection",
  "alternatives": []
}
```

The confidence value is illustrative, not a measured performance claim.

If confidence is low, allow the user to select a game manually.

The selected profile should be saved with the asset.

## Stage 3: Cheap discovery

Goal: identify interesting timestamps cheaply.

Use:

- Low-FPS frame sampling.
- OpenCV motion detection.
- Scene-change detection.
- Audio loudness.
- Spectral flux.
- Audio transient detection.
- OCR on configured HUD regions.
- Voice activity detection.
- Optional speech transcription.

Do not run Qwen on every frame.

Whisper is for speech transcription, not gunshot or explosion recognition. Use a separate audio-event detector or signal-based approach for non-speech sounds.

## Stage 4: Candidate generation

Merge nearby events.

Create candidate windows around interesting timestamps.

Use configurable pre-roll and post-roll.

Initial defaults:

```dotenv
CANDIDATE_PADDING_BEFORE=8
CANDIDATE_PADDING_AFTER=12
```

These values should be adjustable in the UI.

Avoid creating many overlapping candidates for one event.

## Stage 5: Verification

For candidate windows:

1. Extract representative frames.
2. Increase frame sampling density.
3. Run applicable game-specific detectors.
4. Run OCR.
5. Run audio analysis.
6. Run YOLO where useful.
7. Send selected frames to Qwen.
8. Combine evidence.

Qwen should answer specific questions about a candidate rather than make open-ended judgments about a whole VOD.

Example:

"Do these frames show a meaningful gaming event such as a kill, multi-kill, clutch, victory or other notable gameplay moment? Return the likely event type, supporting visual evidence and confidence. If evidence is insufficient, say unknown."

Do not trust Qwen alone for exact event timestamps.

Use the original detector timestamps and event evidence for boundaries.

## Stage 6: Scoring

Initial candidate scoring:

| Signal | Weight |
|---|---:|
| Confirmed game event | 35% |
| Event significance | 20% |
| Audio excitement | 15% |
| Visual intensity | 15% |
| Player reaction/commentary | 10% |
| Context/completeness | 5% |

Store each component separately.

Allow future per-game weight adjustments.

These are initial heuristics, not validated weights.

## Stage 7: Candidate review

Display:

- Candidate preview.
- Start timestamp.
- End timestamp.
- Game.
- Event type.
- Confidence.
- Score breakdown.
- Detector evidence.
- Original source reference.

Allow:

- Accept.
- Reject.
- Trim.
- Extend.
- Merge.
- Export.

## Stage 8: Rendering

Every approved candidate should support two output types.

### Original

- Preserve original aspect ratio.
- Preserve appropriate resolution.
- Preserve audio synchronization.
- Avoid unnecessary transcoding when possible.

### Vertical

- 1080x1920 or configurable 9:16.
- Gameplay-aware crop.
- Optional blurred background.
- Optional facecam layout.
- Optional subtitles.
- Preserve important HUD elements.

Do not use a blind centre crop.

Game profiles should be able to define preferred crop regions and safe areas.

Use FFmpeg.

Consider NVIDIA NVENC for rendering where supported, but maintain CPU fallback.

---

# 8. COMMON EVENT SCHEMA

All detectors must return a common event structure.

Example:

```python
class GameEvent:
    game: str
    event_type: str
    timestamp: float
    confidence: float
    source: str
    evidence: dict
```

Possible event types:

```text
kill
multi_kill
headshot
ace
clutch
round_win
spike_plant
spike_defuse
knockdown
squad_wipe
victory
explosion
combat_start
high_intensity
player_reaction
voice_clip_trigger
```

The common schema must remain independent of any one game.

Game-specific detectors map their signals into this common format.

---

# 9. INITIAL GAME PROFILES

Initial profile targets:

## Valorant

Potential event types:

- Kill.
- Multi-kill.
- Ace.
- Clutch.
- Spike plant.
- Spike defuse.
- Round win.
- Kill feed detection.
- Player reaction.

## CS2

Potential event types:

- Kill.
- Headshot.
- Multi-kill.
- Clutch.
- Bomb plant.
- Bomb defuse.
- Round win.

## PUBG

Potential event types:

- Knockdown.
- Kill.
- Squad wipe.
- Long-range kill.
- Explosion.
- Victory/chicken dinner.

## GTA

Potential event types:

- Police chase.
- Explosion.
- Vehicle crash.
- Pursuit.
- Combat.
- Funny incident.

These are proposed targets. Do not claim out-of-the-box detection for all these events until implemented and validated.

Start with generic detection and add reliable game-specific detectors incrementally.

---

# 10. DATABASE DESIGN

Recommended tables:

```text
users
projects
media_assets
processing_jobs
game_profiles
game_events
clip_candidates
clips
exports
```

Use UUID primary keys.

Store timestamps as seconds, with the source time base and FPS preserved in media metadata.

Store MinIO object keys in the database, not full permanent presigned URLs.

Suggested relationships:

- Project has many media assets.
- Media asset has many processing jobs.
- Media asset has many game events.
- Media asset has many clip candidates.
- Candidate can generate one or more approved clips.
- Clip has multiple exports.

Use Alembic for migrations.

---

# 11. API ENDPOINTS

Initial REST API:

```text
POST   /api/projects
GET    /api/projects
GET    /api/projects/{id}

POST   /api/assets/upload
POST   /api/assets/import-url
GET    /api/assets/{id}

POST   /api/assets/{id}/analyze
GET    /api/jobs/{id}
POST   /api/jobs/{id}/retry

GET    /api/assets/{id}/events
GET    /api/assets/{id}/candidates

PATCH  /api/candidates/{id}
POST   /api/candidates/{id}/approve
POST   /api/candidates/{id}/reject

POST   /api/clips/{id}/export
GET    /api/exports/{id}
GET    /api/game-profiles
```

Use WebSockets or server-sent events for live job progress in the UI if useful.

---

# 12. CELERY QUEUE DESIGN

Use separate queues:

```text
ingest
analysis_cpu
analysis_gpu
verification
render
```

Recommended initial limits:

```dotenv
MAX_CONCURRENT_CPU_JOBS=2
MAX_CONCURRENT_GPU_JOBS=1
```

Store job progress and checkpoints in PostgreSQL.

Use idempotent tasks.

If a worker crashes during verification, the system should resume from the last successful stage instead of redownloading and reprocessing the whole VOD.

---

# 13. MODEL AND LIBRARY SELECTION

| Function | Initial choice |
|---|---|
| Semantic visual verification | Existing Qwen2.5-VL-7B-AWQ vLLM endpoint |
| Object detection | Ultralytics YOLO |
| Speech transcription | faster-whisper |
| OCR | PaddleOCR or Tesseract; benchmark both |
| Audio features | FFmpeg + librosa |
| Motion detection | OpenCV |
| Video metadata | FFprobe |
| Video download | yt-dlp |
| Video rendering | FFmpeg |

Keep the initial model stack minimal.

Avoid downloading large model weights until the relevant detector is actually required.

Check model licenses and dependency licenses separately from repository licenses.

---

# 14. L40S RESOURCE MANAGEMENT

The L40S has approximately 46 GiB of VRAM.

The existing Qwen service may consume a significant portion.

Recommended deployment:

- Existing vLLM/Qwen service stays running.
- FragForge GPU worker uses YOLO and other GPU tasks.
- Qwen is called over HTTP.
- CPU workers perform FFmpeg and most media preparation.
- Start with one GPU task at a time.
- Measure VRAM usage before increasing concurrency.

Avoid loading multiple large models simultaneously without checking actual available VRAM.

Do not modify the existing Qwen deployment or host NVIDIA driver without checking compatibility.

---

# 15. DOCKER DEPLOYMENT PLAN

Create:

```text
/opt/fragforge/
├── app/
├── data/
│   ├── postgres/
│   ├── redis/
│   ├── media/
│   └── models/
└── logs/
```

The repository itself should be cloned into:

```bash
/opt/fragforge/app
```

Prepare environment:

```bash
sudo mkdir -p /opt/fragforge
sudo chown -R "$USER":"$USER" /opt/fragforge

cd /opt/fragforge
mkdir -p data/postgres data/redis data/media data/models logs
```

Check:

```bash
nvidia-smi
docker --version
docker compose version
ffmpeg -version
```

Verify Docker GPU access:

```bash
docker run --rm --gpus all \
  nvidia/cuda:12.8.1-base-ubuntu24.04 \
  nvidia-smi
```

Install NVIDIA Container Toolkit if required:

https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

The Compose application should include:

```text
postgres
redis
api
worker-cpu
worker-gpu
web
```

Use persistent volumes for PostgreSQL and Redis.

Expose only the Next.js frontend and API through localhost-bound ports and Nginx.

Suggested internal port mapping:

```text
FastAPI: 127.0.0.1:8100
Next.js: 127.0.0.1:3100
Existing Qwen: 127.0.0.1:8000
```

Do not expose PostgreSQL or Redis publicly.

For Qwen access from containers:

If Qwen runs on the host, use:

```text
http://host.docker.internal:8000/v1
```

and configure:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

If Qwen runs in Docker, use the appropriate shared Docker network and service/container DNS name instead.

---

# 16. ENVIRONMENT CONFIGURATION

Initial `.env.example`:

```dotenv
APP_ENV=production
APP_SECRET_KEY=CHANGE_ME

WEB_ORIGIN=https://fragforge.example.com

POSTGRES_DB=fragforge
POSTGRES_USER=fragforge
POSTGRES_PASSWORD=CHANGE_ME

REDIS_URL=redis://redis:6379/0

VLM_BASE_URL=http://host.docker.internal:8000/v1
VLM_MODEL=YOUR_ACTUAL_MODEL_ID
VLM_API_KEY=EMPTY

S3_ENDPOINT=http://YOUR_MINIO_HOST:9000
S3_BUCKET=fragforge
S3_ACCESS_KEY=CHANGE_ME
S3_SECRET_KEY=CHANGE_ME
S3_REGION=us-east-1

MAX_CONCURRENT_CPU_JOBS=2
MAX_CONCURRENT_GPU_JOBS=1

FRAME_SAMPLE_FPS=1

CANDIDATE_PADDING_BEFORE=8
CANDIDATE_PADDING_AFTER=12

EXPORT_ORIGINAL=true
EXPORT_VERTICAL=true
```

Do not commit real secrets.

Use a dedicated MinIO service account and private bucket.

---

# 17. DEPLOYMENT COMMANDS

After the repository and Dockerfiles exist:

```bash
cd /opt/fragforge/app

docker compose config

docker compose build

docker compose up -d postgres redis

docker compose up -d api worker-cpu worker-gpu web

docker compose ps

docker compose logs -f api worker-gpu
```

Run migrations:

```bash
docker compose exec api alembic upgrade head
```

The Alembic migration command assumes Alembic has been implemented and the initial migration created.

Nginx should reverse proxy:

```text
https://fragforge.example.com/       -> localhost:3100
https://fragforge.example.com/api/   -> localhost:8100
```

For large video uploads, use browser-to-MinIO presigned uploads rather than proxying files through FastAPI/Nginx.

Configure MinIO CORS for the FragForge domain.

---

# 18. SECURITY REQUIREMENTS

The platform should:

- Use authentication even for a single-user deployment.
- Use private MinIO buckets.
- Use short-lived presigned URLs.
- Keep PostgreSQL and Redis private.
- Keep the Qwen API private.
- Restrict VOD imports to safe public URLs and prevent SSRF.
- Avoid exposing arbitrary command-line parameters to the browser.
- Restrict source file sizes and durations.
- Validate file extensions and actual media types.
- Keep credentials and cookies out of logs.
- Clean temporary frame directories.
- Implement retention and cleanup policies.
- Back up PostgreSQL and critical configuration.

Only process content the user is authorized to access.

---

# 19. IMPLEMENTATION ROADMAP

Estimated work for one developer:

| Phase | Estimated duration |
|---|---:|
| Infrastructure and ingestion | 3–5 days |
| Generic detection engine | 4–6 days |
| Game profile framework | 3–5 days |
| Qwen verification and scoring | 3–4 days |
| Dashboard and review workflow | 5–8 days |
| Rendering | 3–5 days |
| Testing and hardening | 3–5 days |
| Total | 24–38 working days |

These are estimates, not guarantees. Game-specific detector quality may take longer.

## Phase 1 — Infrastructure and ingestion

- Monorepo.
- FastAPI.
- PostgreSQL.
- Redis.
- Celery.
- MinIO.
- Local uploads.
- VOD imports.
- FFprobe metadata.
- Job progress and retry.

Acceptance criteria:

A two-hour video can be uploaded or imported, registered as an asset, and inspected for correct metadata.

## Phase 2 — Generic detection

- Motion analysis.
- Audio features.
- Scene changes.
- OCR.
- Candidate generation.
- Event schema.

Acceptance criteria:

Generate timestamped candidate moments with evidence without needing Qwen.

## Phase 3 — Game profiles

- Automatic game identification.
- Manual override.
- Profile registry.
- Game-specific OCR.
- YOLO integration.
- Initial game profiles.

Acceptance criteria:

Game profiles can be independently enabled and configured.

## Phase 4 — Qwen verification

- Existing vLLM connection.
- Selected-frame extraction.
- Candidate interpretation.
- Confidence and scoring.
- Evidence display.

Acceptance criteria:

Every candidate has a score breakdown and explanation.

## Phase 5 — Dashboard

- Project management.
- Media library.
- Candidate timeline.
- Preview.
- Approve/reject.
- Trim and merge.
- Export settings.

Acceptance criteria:

The workflow is usable without SSH.

## Phase 6 — Rendering

- Original export.
- Vertical export.
- Captions.
- Facecam layout.
- FFmpeg.
- NVENC where supported.

Acceptance criteria:

One approved candidate generates both formats with synchronized audio.

## Phase 7 — Hardening

- Retry/recovery.
- Logging.
- Monitoring.
- Retention.
- Backup.
- Security.
- Performance benchmarks.

Acceptance criteria:

A worker restart does not lose project state or require complete reprocessing.

---

# 20. TESTING AND PERFORMANCE METRICS

Create a small dataset of actual gaming footage for each initial profile.

Include both exciting and ordinary moments.

Track:

1. Candidate recall against manually labelled events.
2. Candidate precision.
3. Clip boundary quality.
4. Processing time / source duration.
5. Candidate count per hour.
6. Percentage of accepted candidates.
7. GPU VRAM peak.
8. GPU inference latency.
9. Export time per output minute.
10. Storage consumption per source hour.
11. Worker restart recovery.
12. Audio synchronization.

Do not claim detection accuracy or processing speed without actual measurements.

The most useful product metric is the number of genuinely useful clips discovered per hour of footage.

---

# 21. MVP SCOPE

The first usable version should contain:

- Local video upload.
- Public Twitch/YouTube VOD import.
- Game identification.
- Manual game override.
- Generic motion detection.
- Generic audio detection.
- OCR-based detection.
- Candidate timestamp generation.
- Qwen verification.
- Candidate scoring.
- Review and approval.
- Clip trimming.
- Original-resolution MP4 export.
- Vertical 9:16 export.
- Optional captions.
- Persistent job status.
- Retry and resume.

Explicitly defer:

- Live OBS clipping.
- Automatic publishing to social platforms.
- Multi-user collaboration.
- Advanced face tracking.
- Model training.
- Complex automated editing and transitions.

---

# 22. NEXT ACTIONS FOR THE IMPLEMENTATION AGENT

Do not immediately generate a huge application in one response.

Proceed in controlled engineering steps.

## Task 1: Infrastructure clarification

Ask the user one question:

Is the existing L40S Qwen2.5-VL vLLM service running:

A. Directly on the Linux host, or  
B. Inside Docker?

This determines service networking.

## Task 2: Repository audit

Clone and inspect:

- https://github.com/bendawg2010/Auto-clipper
- https://github.com/mehbul/chopify
- https://github.com/rfypych/video-highlight-detection
- https://github.com/VicelikeElm/OBS-AI-Highlights

Pin commits and inspect actual source code, tests, dependencies, model requirements and licenses.

Produce a concise audit report with:

- Reusable modules.
- Modules to rewrite.
- Dependency conflicts.
- Runtime requirements.
- Model requirements.
- License risks.
- Integration recommendations.

## Task 3: Create the FragForge skeleton

Implement:

- Monorepo.
- Docker Compose.
- FastAPI.
- Next.js.
- PostgreSQL.
- Redis.
- Celery.
- MinIO client.
- Alembic.
- Health checks.
- Environment configuration.

Do not start by implementing advanced AI detection.

## Task 4: Implement ingestion

Create working local upload and URL import.

Make sure the application can:

1. Accept a local video.
2. Upload it to MinIO.
3. Create a media asset record.
4. Probe metadata.
5. Queue processing.
6. Display progress.
7. Recover after worker restart.

## Task 5: Implement generic detection

Implement low-cost discovery using OpenCV, audio analysis and OCR.

Generate candidate timestamps.

Validate on actual gameplay.

## Task 6: Integrate existing Qwen

Connect to the existing vLLM endpoint.

Send only selected candidate frames.

Never send entire multi-hour recordings.

## Task 7: Build game profiles

Start with one game and validate the entire detector path before adding more.

Recommended first targets: Valorant or CS2 if the user can provide representative footage.

## Task 8: Build review and rendering

Implement original and vertical exports.

Test audio synchronization and game HUD preservation.

## Task 9: Production hardening

Add authentication, backups, storage lifecycle management, logs and recovery.

---

# 23. IMPORTANT DEVELOPMENT PRINCIPLES

1. Build a unified FragForge application, not four independent applications.
2. Do not duplicate the existing Qwen deployment.
3. Do not run heavy AI inference on the FastAPI process.
4. Keep CPU and GPU workers separate.
5. Keep the event schema game-independent.
6. Use game profiles for game-specific intelligence.
7. Store event evidence and score breakdowns.
8. Keep source media in MinIO.
9. Persist job state in PostgreSQL.
10. Use idempotent processing stages.
11. Make video processing resumable.
12. Preserve original media timestamps.
13. Avoid blind centre cropping for vertical exports.
14. Validate actual source code before reusing repository modules.
15. Verify all licenses and model terms.
16. Do not invent detection capabilities or performance figures.
17. Prioritise a reliable generic MVP before adding advanced AI.
18. Keep live-mode support as a future extension.

---

# 24. FINAL PRODUCT VISION

FragForge should eventually function as a personal AI gaming content studio.

The user uploads a two-hour gameplay recording or submits a public VOD URL.

FragForge automatically:

1. Imports the recording.
2. Identifies the game.
3. Scans the footage.
4. Detects potential highlights.
5. Verifies selected moments.
6. Scores the candidates.
7. Presents a timeline of interesting events.
8. Allows the user to review and trim clips.
9. Exports original and vertical versions.
10. Stores all results in a reusable project library.

The user should be able to process multiple games and recordings without manually defining every timestamp.

The platform should be modular enough to add new game profiles and detection models without changing its core ingestion, job, storage or rendering architecture.

**Immediate next step: Audit the repositories, clarify the Qwen deployment networking, and create the initial FragForge monorepo and infrastructure.**

END OF HANDOFF DOCUMENT.
