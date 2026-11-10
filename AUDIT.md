# clippie reference-repo audit

Cloned shallow into `audit/` (not part of clippie). Findings come from file structure, dependency lists and license files. I have **not** run any of the code or tests yet.

## Pinned commits

| Repo | Commit | Python LOC | Tests | License file |
|---|---|---:|---|---|
| bendawg2010/Auto-clipper | `73d45a45335c6d03f90f95e4e6b23e40cf343fba` | ~13.2k | `tests/test_smoke.py` only | MIT |
| mehbul/chopify | `d576927bf3b764733c97ac1ad324aa9957b0885d` | ~10.4k | 16 test files, pytest | MIT |
| rfypych/video-highlight-detection | `9f10020e082a69456f12af106b3b07828eb6e27b` | ~1.4k | none | **None in repo** (README says MIT) |
| VicelikeElm/OBS-AI-Highlights | `a6c6e1832459eee12bcf8aa8012e132a6f58fff6` | ~17k | 7 test files | **None in repo** |

## License risks (most important finding)

1. **OBS-AI-Highlights has no LICENSE file.** Without one, all rights are reserved by default. Do not copy code. Use it only as a design reference for the future live mode, or ask the author for a license.
2. **video-highlight-detection has no LICENSE file.** The README claims MIT but the badge links to a missing `LICENSE`. This is ambiguous, so treat it as reference-only, or ask the author to add the file. It is small (~740 lines of detectors), so rewriting is cheap.
3. **Ultralytics YOLO is AGPL-3.0** (Auto-clipper depends on it). Using it in a self-hosted app you don't distribute or expose as a network service to others is generally fine, but check this before any multi-user or hosted offering. Alternatives: an Apache/MIT-licensed detector, or an Ultralytics enterprise license.
4. Auto-clipper's bundled `models/best.pt` is a YOLOv11n fine-tuned on an Arc Raiders Roboflow dataset. It is useless for Valorant, CS2, PUBG or GTA, and the dataset's license is unverified. Don't ship it.
5. Model and dependency licenses (Whisper, PaddleOCR, Tesseract, etc.) still need a separate check.

## Per-repo assessment

### Auto-clipper (MIT)
- Layout: `analysis/` has `motion_detector`, `audio_detector`, `scene_detector`, `hybrid_detector`, `yolo_local_analyzer`, `clip_trigger_detector`, and a 4,016-line `game_profiles.py`.
- Reuse (rewrite against the clippie `GameEvent` schema, taking logic and not the files):
  - `motion_detector.py` (215), `audio_detector.py` (217), `scene_detector.py` (232): small, generic.
  - `hybrid_detector.py` (384): the fusion pattern.
  - `game_profiles.py`: the profile structure only. Its size suggests mostly data and heuristics tied to its own games.
- Rewrite or skip: `arc_clip_detector.py` (1,733 lines, Arc Raiders-specific), the Flask app, pywebview desktop shell, and the Roboflow analyzers (cloud and API-key dependent).
- Test coverage is a single smoke test, so don't assume the detectors are validated.
- No Valorant, CS2, PUBG or GTA profile was confirmed. I did not read the profile data, so check `game_profiles.py` before relying on this.

### Chopify (MIT)
- The most mature repo (16 tests). Modules: `ingest.py`, `visual.py`, `score_clips.py`, `sections.py`, `tighten.py`, `captions.py`, `render_clips.py`, `evidence.py`, `semantic.py`, `projects.py`.
- Reuse as design and algorithm reference: resumable pipeline stages, audio-first scoring, `tighten.py` (clip boundary tightening), `captions.py` (ASS burn-in via FFmpeg), `render_clips.py` (FFmpeg patterns).
- Dependencies are light (yt-dlp, faster-whisper, opencv, numpy), so conflicts are unlikely.
- Skip: the dashboard (single-trusted-user design) and its file-based project store. Replace both with PostgreSQL and MinIO.
- The coarse 160x90 / 2 FPS visual baseline is fine for discovery but not for event verification, as the handoff already notes.

### video-highlight-detection (license unclear)
- Three small detectors: `audio_detector` (259), `ocr_detector` (231), `visual_detector` (245). They use librosa, pydub and pytesseract.
- Minecraft-oriented. Rewrite from scratch using its approach (OCR on a configured kill-feed region, audio spikes) rather than copying.

### OBS-AI-Highlights (no license)
- Rich future-live-mode reference (OBS WebSocket, `round_tracker`, `game_timeline`, `layout_analysis`, `verify_clips`), plus JSON profiles for destiny2, minecraft, minecraft_bedrock, siege and tarkov.
- Windows-specific dependencies (PyAudioWPatch, sv_ttk, mss). Not suitable for a Linux server.
- Revisit only for the deferred live mode, and only after the licensing is resolved.

## Dependency and runtime notes

- Common to all: FFmpeg, yt-dlp, OpenCV, numpy, faster-whisper. These match the planned stack.
- Conflicting choices to resolve in clippie: `opencv-python` vs `opencv-python-headless` (use headless in containers), `openai-whisper` vs `faster-whisper` (use faster-whisper only), Tesseract vs PaddleOCR (still to benchmark).
- GPU: only YOLO needs the GPU worker. Qwen stays on the existing host service.

## Integration recommendations

1. **Write clippie's detectors fresh** against the `GameEvent` schema, using Auto-clipper's and Chopify's logic as reference. Both are MIT, so porting small pieces with attribution is fine.
2. **Treat the two unlicensed repos as read-only references** until their authors add licenses.
3. **Don't reuse any bundled model weights.** Start with generic detection, then train or choose per-game detectors later.
4. **First game profile:** Valorant or CS2. Neither exists in the audited repos, so this is new work, and it needs footage from you.
5. **Decide on the AGPL question** for Ultralytics now, or plan to swap it. This is the only blocker-level license item for a personal deployment.

## Resolved

- Qwen vLLM runs **directly on the host**. Containers will use `VLM_BASE_URL=http://host.docker.internal:8000/v1` with `extra_hosts: host.docker.internal:host-gateway`. Read the real model ID from `curl http://127.0.0.1:8000/v1/models`.
- Note that vLLM must bind to an address reachable from the Docker bridge (not only `127.0.0.1`) for this to work. Check that when we deploy.
