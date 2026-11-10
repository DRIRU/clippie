<div align="center">

# 🎬 clippie

**Paste a gaming video link. Get ranked highlight clips, ready for YouTube Shorts, TikTok and Reels.**

Self-hosted · local-first · no paid AI APIs

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?logo=ffmpeg&logoColor=white)
![Status](https://img.shields.io/badge/status-prototype-orange)

</div>

---

## ✨ What it does

```
 YouTube / Twitch link ─┐
                        ├─▶  probe  ─▶  analyze  ─▶  ranked candidates  ─▶  review  ─▶  export
 local video upload  ───┘                                                    (trim ·       ├─ original aspect
                                                                          approve ·       └─ vertical 9:16
                                                                           reject)
```

| | |
|---|---|
| 🔗 **Paste & go** | Drop a YouTube link, it downloads, analyzes automatically and lists candidates. Or upload a file. |
| 🔊 **Finds the loud, busy moments** | Audio loudness, spectral change, motion and scene cuts fused into a score with a visible breakdown. |
| 🎯 **Game profiles** *(experimental)* | Reads the Valorant kill feed with OCR to find *your* kills, multi-kills, and ACE / CLUTCH banners. |
| ✂️ **Review in the browser** | Preview each clip, nudge start and end, approve or reject. |
| 📱 **Two exports per clip** | Original aspect ratio, and 1080×1920 vertical with a blurred background (no blind centre crop). |
| 🔁 **Survives restarts** | Jobs live in SQLite with progress and retry. Finished analysis stages are checkpointed and skipped on resume. |
| 🪶 **Runs anywhere** | SQLite + local folder + in-process workers. No Docker, Postgres, Redis or MinIO needed. |

## 🚀 Quick start

You need **Python 3.11+** and **ffmpeg / ffprobe** on your PATH. For YouTube links, also have **Node.js** (or Deno/Bun) installed. YouTube downloads need a JavaScript runtime.

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

## 🎮 Game profiles

Pick a game per video in the UI. Today there are two options:

| Profile | How it works | Status |
|---|---|---|
| **Generic** | Audio + motion + scene-cut fusion. Game-agnostic. | Works, not validated on real gameplay |
| **Valorant** | OCRs the top-right kill feed (RapidOCR, runs on CPU, no system install) and counts rows where the left name matches **your in-game name**. Emits `kill`, `multi_kill`, `ace` and `clutch`. | Experimental |

With a profile active, candidates are scored with confirmed game events weighted highest (35% event, 20% significance, 15% audio, 15% visual, 5% scene cuts, renormalised because reaction and context aren't built yet). The full breakdown is stored on every candidate.

> ⚠️ **Honest status:** the Valorant profile has only been tested on *synthetic* kill feeds (clean text, no gameplay behind it). Screen regions and thresholds are uncalibrated guesses, and real-world accuracy is unknown until it's run on real recordings with labelled highlights. OCR speed was about 0.3 s per frame in that synthetic test, so roughly 35 min per source hour at 1 fps is an estimate, not a measurement.

## 🧪 Tests

```bash
.venv/Scripts/python -m pytest -q          # includes real ffmpeg renders and OCR
.venv/Scripts/python -m tools.bench_ocr    # OCR smoke benchmark on a synthetic kill feed
```

## ⚙️ Configuration

Environment variables with the `CLIPPIE_` prefix (see [`.env.example`](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `CLIPPIE_DATA_DIR` | `./data` | Database, media and analysis checkpoints |
| `CLIPPIE_MAX_WORKERS` | `2` | Concurrent in-process jobs |
| `CLIPPIE_CANDIDATE_PADDING_BEFORE` / `_AFTER` | `8` / `12` | Seconds around each detected moment |
| `CLIPPIE_MAX_UPLOAD_GB` / `CLIPPIE_MAX_DURATION_HOURS` | `20` / `6` | Input limits |
| `CLIPPIE_USE_NVENC` | `false` | Use NVIDIA NVENC for rendering |

## 🛡️ Security notes

- **No authentication yet.** The server binds to `127.0.0.1` only. Do not expose it to a network.
- URL imports accept only `http(s)` and refuse hosts that resolve to private addresses. The hostname is checked once, so redirects followed by yt-dlp are not re-validated.
- Upload type and size limits apply, and files are validated with ffprobe.

## 🗺️ Roadmap

See [`PLAN.md`](PLAN.md) for the phased plan and [`AUDIT.md`](AUDIT.md) for the research on the open-source projects this builds on.

- [x] Ingestion: upload and YouTube / URL import
- [x] Generic detection and candidate ranking
- [x] Review UI and original + vertical export
- [x] First game profile (Valorant, experimental)
- [ ] Qwen2.5-VL verification of candidate frames
- [ ] Captions and facecam layouts
- [ ] More games (CS2, PUBG, GTA), automatic game identification
- [ ] Postgres, S3/MinIO, Celery and Redis for the full deployment
- [ ] Authentication, retention and cleanup

## 📁 Project layout

```
clippie/
├── api.py            FastAPI app and routes
├── pipeline.py       import · ingest · analyze · export job handlers
├── jobs.py           in-process job runner with persistence and retry
├── media.py          ffprobe, thumbnails, safe URL import (yt-dlp)
├── rendering.py      original and vertical ffmpeg exports
├── detection/        audio and motion signals, OCR, candidate fusion
├── profiles/         game profile framework + Valorant
└── static/           the web UI
```
