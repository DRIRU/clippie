<div align="center">

# clippie

**Paste a gaming video link. Get ranked highlight clips, ready for YouTube Shorts, TikTok and Reels.**

Self-hosted · local-first · no paid AI APIs

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?logo=ffmpeg&logoColor=white)
![Status](https://img.shields.io/badge/status-prototype-orange)

</div>

---

## What it does

```
 YouTube / Twitch link ─┐
                        ├─▶  prepare ─▶ analyze ─▶ AI check ─▶ trimmed clips ─▶ review ─▶ export (one or many)
 local video upload  ───┘    (live step-by-step progress)       ready to play   approve    ├─ original aspect
                                                                                reject    └─ vertical 9:16   (pick one)
                                                                                adjust
```

| | |
|---|---|
| **Paste and go** | Drop a YouTube link; it downloads, analyzes automatically and lists clips. Or upload a file. |
| **Live pipeline progress** | Right after you send a link, a step tracker shows Download, Prepare, Audio, Motion, Game HUD, Find highlights, AI check and Trim clips, with percentages and a Retry button if a step fails. |
| **Ready-to-watch clips** | Every highlight is rendered as its own short, trimmed clip. Press play and watch it; no scrubbing through the full video and no timestamps to read. |
| **Finds the loud, busy moments** | Audio loudness, spectral change, motion and scene cuts are fused into a score with a visible breakdown. |
| **Game profiles** *(experimental)* | Valorant (your kills, multi-kills, ACE / CLUTCH), Among Us (meetings, ejections, results) and GTA (WASTED, BUSTED, mission banners), found by reading on-screen text with OCR. |
| **AI verification** *(optional)* | Sends a few frames per clip to your own OpenAI-compatible vision model (for example vLLM serving Qwen2.5-VL) and shows a verdict with a one-line explanation. |
| **Big, customizable subtitles** | Local speech-to-text (faster-whisper) on just each clip, shown live over the clip and burned into exports. Choose size (Small to Huge, or any size), font, colours, outline or solid box, position, words per line, ALL CAPS, and a highlighted spoken word, or start from a preset (Bold, Clean, Boxed, Shorts). |
| **Review in the browser** | Approve or reject, and adjust a clip's length with 2-second nudges; it is re-trimmed automatically. |
| **Facecam layout** | For vertical exports, put your webcam in its own band on top and the gameplay below. You drag a box over your webcam on a frame of your video, and a live 9:16 preview, rendered by the same ffmpeg filter as the export, shows exactly what you will get. |
| **One format per export** | Choose **Vertical 9:16** (1080×1920 with a blurred background, no blind centre crop) or **Horizontal** (the original aspect ratio). One is enough; your choice is remembered. |
| **Bulk export** | Tick any clips, or leave none ticked to export every approved clip, in one click. Then download all the finished files as a single zip. |
| **Delete anything** | Remove a video and all of its clips, exports and files. Running jobs are cancelled. |
| **Survives restarts** | Jobs live in SQLite with progress and retry. Finished analysis stages are checkpointed and skipped on resume. |
| **Runs anywhere** | SQLite + local folder + in-process workers. No Docker, Postgres, Redis or MinIO needed. |

## Quick start

You need **Python 3.11+** and **ffmpeg / ffprobe** on your PATH. For YouTube links, also install **Node.js** (or Deno/Bun): current YouTube downloads need a JavaScript runtime.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Linux/macOS: .venv/bin/python
.venv/Scripts/python run.py
```

Open **http://127.0.0.1:8100**, paste a link, and click **Import**.

Want something to try without a real video?

```bash
.venv/Scripts/python -m tools.make_samples    # writes synthetic clips into sample/
```

After pulling an update, restart the server (Ctrl+C, then run it again) so the backend matches the page you see.

## Using it

1. **Add a video.** Paste a YouTube or Twitch link, or switch to **Upload**. Pick a game profile (and your in-game name for Valorant) and leave *Analyze automatically* on.
2. **Watch the pipeline.** The tracker shows which step is running and how far along it is. The library entry on the left mirrors it.
3. **Review clips.** Each clip appears already trimmed. Play it, check the AI note if enabled, then **Approve** or **Reject**. Use **Adjust length** to make a clip start earlier, end later, and so on; it is re-trimmed automatically.
4. **Export.** Pick a **Format** (Vertical 9:16 or Horizontal) above the clips. **Export** on a clip renders just that clip. For many clips, tick the ones you want (or **Select all**) and use **Export N selected**; with nothing ticked the button exports **all approved clips**. Each finished file has a download link, and **Download zip** gives you every finished export for the chosen format in one file (`clip-01-vertical.mp4`, `clip-02-vertical.mp4`, ...).
5. **Subtitles (optional).** Tick *Add subtitles to clips* when adding a video, or open the **Subtitles** panel and click **Generate subtitles**. Style them with the controls (the preview on every clip updates as you drag), then turn on *Show on clips and burn into exports*. Exports then include them.
6. **Facecam layout (optional).** Open the **Vertical layout** panel, choose *Facecam on top*, and drag a box around your webcam on the frame shown. Adjust the band height and whether the gameplay shows the whole frame or is cropped to fill. Vertical exports use it from then on.
7. **Clean up.** **Delete** removes a video and everything derived from it.

Videos analyzed before clip trimming existed have no trimmed clips yet; use **Re-analyze** on them.

## Subtitles

- **How it works:** each clip's audio (only that span) is transcribed locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). You get timed words, which are grouped into short on-screen chunks. Nothing is sent to an online service.
- **First use downloads a speech model** from Hugging Face the first time subtitles are generated (`base` is about 145 MB; `tiny` about 75 MB). Pick the model with `CLIPPIE_WHISPER_MODEL`. Larger models are more accurate and slower. `.en` variants are English only.
- **Customizable:** text size (presets Small, Medium, Large, Huge, or a 2% to 18% slider, as a share of the video height), position and distance from the edge, font, bold, ALL CAPS, words shown at a time, text / spoken-word / outline colours, outline thickness, and an optional solid box with adjustable opacity. Settings are saved per video.
- **Burned-in exports:** original and vertical 9:16 exports render the same style with ffmpeg's subtitle renderer. Sizes are relative to each export's own height, so the vertical export scales them up automatically.
- **Trimming a clip** clears its subtitles and transcribes the new span automatically.
- **CPU or GPU:** with the default `auto` setting an NVIDIA GPU is used when its CUDA libraries are installed, otherwise it falls back to the CPU automatically. In a small test, a 14-second clip took about 3 seconds on the CPU and about 0.3 seconds on a GTX 1650 (`tiny.en`).
- **Enabling the GPU:** install the CUDA 12 libraries into the same environment (about 1 GB):

```bash
.venv/Scripts/python -m pip install -r requirements-gpu.txt
```

  On Windows clippie finds them automatically. On Linux, install the CUDA 12 and cuDNN 9 libraries system-wide instead.

> **Status:** checked with real speech (Windows text-to-speech) and the `tiny.en` model, and with mocked transcription in the tests, including pixel checks that the text is burned into both export shapes. It has **not** been evaluated on real gameplay audio, which is noisy and full of game sound, so expect more transcription mistakes there. Fonts come from your system; if a font is missing, the renderer substitutes another.

## Vertical layouts

Vertical (9:16, 1080x1920) exports have two layouts, set per video in the **Vertical layout** panel:

| Layout | What you get |
|---|---|
| **Blurred background** (default) | The whole frame, centred on a blurred, enlarged copy of itself. No blind centre crop. |
| **Facecam on top** | Your webcam in a band at the top (15% to 45% of the height) and the gameplay below it. |

- **You mark the webcam yourself.** Pick a frame where it is visible, then drag a box around it. Nothing is auto-detected, so it works for any webcam position or overlay style.
- **Gameplay area:** *Whole frame* keeps everything visible on a blurred fill; *Fill and crop* crops to fill the area, and you choose which part of the width to keep (left, centre or right) so the HUD you care about stays in view.
- **Preview before you export:** the preview image is rendered with the same filter graph as the export, so what you see is what you get.
- With *Whole frame*, the webcam also remains visible inside the gameplay picture, because the layout copies it to the top band rather than removing it from the frame. Horizontal exports are unaffected.
- Subtitles still burn in at the bottom of the finished vertical frame.
- **Speed:** the blurred fill is computed on a small copy of the frame and scaled back up, which looks the same (SSIM 0.997 against a full-size blur) but is far cheaper. Vertical exports are also capped at 30 fps for 60 fps sources (`CLIPPIE_VERTICAL_MAX_FPS`). In a test on a 1080p60 source, a 20-second vertical clip rendered in about 9 seconds instead of 77 (about 16 seconds if you keep 60 fps). Timings vary by machine and footage.

> **Status:** tested with synthetic video and pixel checks (the right pixels end up in the top band and the gameplay area), not yet with real streamer footage. If your webcam box is a different shape from the band it is scaled to fill it, which crops a little of the edges; draw the box close to the band's shape (wide, about 1080 by 576 for the default) for the cleanest result.

## Game profiles

Pick a game per video in the UI. Today there are four options:

| Profile | How it works | Events | Status |
|---|---|---|---|
| **Generic** | Audio + motion + scene-cut fusion. Game-agnostic. | `high_intensity` | Works, not validated on real gameplay |
| **Valorant** | OCRs the top-right kill feed (RapidOCR, runs on CPU, no system install) and counts rows where the left name matches **your in-game name**. | `kill`, `multi_kill`, `ace`, `clutch` | Experimental |
| **Among Us** | OCRs large centre-screen text (English): meeting splashes, ejection lines and result screens. No player name needed. | `body_reported`, `emergency_meeting`, `impostor_ejected`, `crewmate_ejected`, `victory`, `defeat` | Experimental |
| **GTA** | OCRs the centre-screen banners. Crashes, explosions and chases have no text, so they come from the generic audio and motion scoring that is fused in. Wanted-level stars are not detected. No player name needed. | `wasted`, `busted`, `mission_passed`, `mission_failed` | Experimental |

Adding a game that is recognised by on-screen text is small: a region, a list of phrase rules and their significance (see `profiles/among_us.py` and the shared `profiles/banners.py`).

With a profile active, candidates are scored with confirmed game events weighted highest (35% event, 20% significance, 15% audio, 15% visual, 5% scene cuts, renormalised because reaction and context aren't built yet). The full breakdown is stored on every candidate.

> **Status:** all three game profiles have only been tested on *synthetic* rendered text (clean text on a plain background, no real gameplay). Screen regions, wording and thresholds are uncalibrated guesses, and real-world accuracy is unknown until they are run on real recordings with labelled highlights. Banner wording differs between game versions and languages; these profiles expect English text. OCR speed was about 0.3 s per frame in that synthetic test, so roughly 35 minutes per source hour at 1 fps is an estimate, not a measurement.

## AI verification (optional)

Point clippie at an existing OpenAI-compatible vision endpoint and every clip gets checked after analysis:

```bash
CLIPPIE_VLM_BASE_URL=http://127.0.0.1:8000/v1 .venv/Scripts/python run.py
```

Or put it in a local `.env` file (git-ignored) so you don't retype it:

```
CLIPPIE_VLM_BASE_URL=http://127.0.0.1:8000/v1
```

- The model name is read from `GET /v1/models` unless you set `CLIPPIE_VLM_MODEL`. clippie never loads or deploys a model.
- Only a handful of frames per clip are sent (default 6, at most 4 per request), never the whole video. The model may answer *unknown*.
- If the server allows only one image per prompt, clippie retries once with the frames tiled into a single image.
- The model never sets timestamps; detectors keep control of clip boundaries.
- Score fusion is a simple heuristic: a confirmation nudges the detector score up, a rejection halves it, and unsure or failed checks leave it unchanged. The original detector score is kept in the breakdown, and adjusting a clip's length clears its verdict.
- Verification is resumable: re-running skips clips that already have a verdict.
- No GPU handy? `python -m tools.fake_vlm` runs a stand-in server with canned replies, only for trying the flow.

> **Status:** checked end to end against a real vLLM Qwen2.5-VL-7B-AWQ endpoint on a synthetic clip, where it read an on-screen banner correctly, and against mocked servers in the tests. It has **not** been evaluated on real gameplay, so prompt wording and the score blend are unvalidated. With a 4096-token model context (a common vLLM default) the default settings fit; raising `--max-model-len` on the server lets you send more frames.

## Tests

```bash
.venv/Scripts/python -m pytest -q          # includes real ffmpeg renders and OCR
.venv/Scripts/python -m tools.bench_ocr    # OCR smoke benchmark on a synthetic kill feed
```

The tests use a temporary data folder and ignore your `.env`, so they never touch your library or your AI endpoint.

## Configuration

Environment variables with the `CLIPPIE_` prefix, or the same keys in a `.env` file (see [`.env.example`](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `CLIPPIE_DATA_DIR` | `./data` | Database, media and analysis checkpoints |
| `CLIPPIE_MAX_WORKERS` | `2` | Concurrent in-process jobs |
| `CLIPPIE_CANDIDATE_PADDING_BEFORE` / `_AFTER` | `8` / `12` | Seconds around each detected moment |
| `CLIPPIE_MAX_UPLOAD_GB` / `CLIPPIE_MAX_DURATION_HOURS` | `20` / `6` | Input limits |
| `CLIPPIE_USE_NVENC` | `false` | Use NVIDIA NVENC for exports |
| `CLIPPIE_VERTICAL_MAX_FPS` | `30` | Cap vertical exports at this frame rate when the source is faster (`0` keeps the source rate). Sources at or below the cap are never changed. |
| `CLIPPIE_VLM_BASE_URL` | unset | OpenAI-compatible endpoint; AI verification is off when unset |
| `CLIPPIE_VLM_MODEL` | auto | Model id; defaults to the first one the server reports |
| `CLIPPIE_VLM_FRAMES` / `CLIPPIE_VLM_MAX_IMAGES` | `6` / `4` | Frames sampled per clip / images per request |
| `CLIPPIE_AUTO_VERIFY` | `true` | Verify automatically after analysis when the endpoint is set |
| `CLIPPIE_WHISPER_MODEL` | `base` | Speech model for subtitles (`tiny`, `base`, `small`, `medium`, `.en` variants) |
| `CLIPPIE_WHISPER_DEVICE` / `CLIPPIE_WHISPER_COMPUTE_TYPE` | `auto` / `auto` | `cpu` or `cuda`; `auto` uses the GPU when its libraries are installed and falls back to the CPU otherwise |
| `CLIPPIE_WHISPER_LANGUAGE` | detect | Force a language code such as `en` |
| `CLIPPIE_AUTO_CAPTIONS` | `false` | Transcribe every new video without being asked |

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| YouTube import fails with "video is unavailable" or a sign-in message | Install Node.js (or Deno/Bun) and restart. Age-restricted, private and members-only videos, and YouTube bot checks, cannot be imported; download the video yourself and use **Upload**. |
| The page looks empty or broken after an update | The server is still running old code. Restart it. |
| Port 8100 is already in use | Another copy is running. Stop it before starting a new one. |
| `ffmpeg` or `ffprobe` not found | Install FFmpeg and make sure both are on your PATH. |
| Subtitles fail or never start | Check that `pip install -r requirements.txt` completed (it includes `faster-whisper`) and that the machine can reach Hugging Face for the first model download. If a GPU is detected but its CUDA libraries are missing, clippie falls back to the CPU on its own. |
| Subtitle text looks wrong in an export | The font may not be installed on this machine; choose a different font in the Subtitles panel. |
| A video won't delete cleanly on Windows | The browser may still be streaming it. Deleting retries for a few seconds; if it still fails, close the tab and delete again. |

## Security notes

- **No authentication yet.** The server binds to `127.0.0.1` only. Do not expose it to a network.
- URL imports accept only `http(s)` and refuse hosts that resolve to private addresses. The hostname is checked once, so redirects followed by yt-dlp are not re-validated.
- Upload type and size limits apply, and files are validated with ffprobe.

## Roadmap

See [`PLAN.md`](PLAN.md) for the phased plan and [`AUDIT.md`](AUDIT.md) for the research on the open-source projects this builds on.

- [x] Ingestion: upload and YouTube / URL import
- [x] Generic detection and candidate ranking
- [x] Review UI, trimmed preview clips, and original + vertical export
- [x] Live pipeline progress and video deletion
- [x] First game profile (Valorant, experimental)
- [x] Vision-model verification of clips (checked on a synthetic clip only)
- [x] Among Us and GTA profiles (experimental)
- [ ] Calibrating the game profiles on real footage
- [x] Subtitles: local transcription, live preview, burned-in exports
- [x] Facecam layout for vertical exports
- [ ] More games (CS2, PUBG), automatic game identification
- [ ] Postgres, S3/MinIO, Celery and Redis for the full deployment
- [ ] Authentication, retention and cleanup

## Project layout

```
clippie/
├── api.py            FastAPI app and routes
├── pipeline.py       import · ingest · analyze · preview · verify · export job handlers
├── jobs.py           in-process job runner with persistence, retry and cancellation
├── library.py        deleting a video and everything derived from it
├── progress.py       turns a video's jobs into pipeline steps for the UI
├── media.py          ffprobe, thumbnails, safe URL import (yt-dlp)
├── rendering.py      preview, original and vertical ffmpeg renders
├── layout.py         vertical layouts (blurred background, facecam on top)
├── vlm.py            vision-model verification client
├── captions.py       speech-to-text, caption styles, ASS subtitle output
├── storage.py        local storage backend (S3 planned)
├── detection/        audio and motion signals, OCR, candidate fusion
├── profiles/         game profile framework: Valorant, Among Us, GTA (+ shared banner rules)
└── static/           the web UI
tests/                pytest suite (real ffmpeg, OCR, mocked vision server)
tools/                sample generator, OCR benchmark, stand-in vision server
```
