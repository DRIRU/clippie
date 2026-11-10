# clippie (barebone prototype)

Phases 1-4 of `PLAN.md` (Phase 4 = first game profile, experimental), in barebone mode: SQLite + local-directory storage + in-process job workers. No Docker, Postgres, Redis or MinIO needed.

## Run

Requires Python 3.11+, `ffmpeg` and `ffprobe` on PATH.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python
.venv/Scripts/python run.py
```

Open http://127.0.0.1:8100. Or paste a YouTube link into the import box (it downloads, analyzes automatically, and lists candidates). Run `python -m tools.make_samples` to generate synthetic test clips in `sample/` (a loud, busy burst at 40-48 s, and a fake Valorant kill feed).

Flow: upload or import a URL -> it is probed and gets a thumbnail -> **Analyze** -> review candidates (trim, approve, reject) -> **Export original + vertical** -> download.

Tests (include real ffmpeg renders): `.venv/Scripts/python -m pytest -q`

## What works
- Upload (extension allow-list, size limit, ffprobe validation) and public-URL import via yt-dlp (SSRF guard, no user-supplied yt-dlp options).
- Jobs persisted in SQLite with progress, retry, and automatic re-queue after a restart. Analysis stages are checkpointed (`data/media/analysis/<asset>/`), so a restart skips finished stages.
- Generic detection: audio loudness + spectral flux, low-res motion, scene cuts, fused into ranked candidates with a stored score breakdown and events.
- Export: original aspect ratio, and 1080x1920 vertical with a blurred background (no blind centre crop). Libx264 by default; `CLIPPIE_USE_NVENC=true` for NVENC.
- Files are served with HTTP Range support so the player can seek.

## Game profiles (Phase 4, experimental)
- Pick a game per video in the UI (manual override; auto-identification is not built). Only **Valorant** exists besides Generic.
- Valorant: OCRs the top-right kill feed (RapidOCR, ONNX, no GPU or system install needed) and counts rows where the **left name matches your in-game name**, which you must enter. Derives `kill`, `multi_kill` (kills <=6 s apart), `ace` (5 kills in 120 s, heuristic) and reads `ACE` / `CLUTCH` banners.
- Scoring with a profile uses the plan weights (confirmed event 35%, significance 20%, audio 15%, visual 15%, scene 5%), renormalised because reaction/context aren't implemented. The breakdown is stored per candidate.
- Raw OCR is checkpointed, so changing the name or rules re-derives events without re-running OCR.
- `python -m tools.bench_ocr` runs an OCR smoke benchmark on a synthetic kill feed.

**Validation status:** tested only on *synthetic* rendered kill feeds (clean text, no weapon icons, no gameplay). Screen regions and thresholds are uncalibrated guesses. Real accuracy is unknown until it's run on real Valorant recordings with labelled highlights. Speed: about 0.3 s per OCR'd frame on this machine's CPU in the synthetic test, i.e. roughly 35 min per region per source hour at 1 fps (an estimate, not measured on real footage).
- Tesseract and PaddleOCR were not benchmarked: Tesseract isn't installed here and Paddle is heavy. The backend interface (`detection/ocr.py`) is ready for either.

## Not yet (see PLAN.md)
- No authentication: the server binds to 127.0.0.1 only. Do not expose it.
- Generic mode is unvalidated: it finds loud, busy moments, not specific kills. Qwen verification (Phase 5), captions and facecam layouts (Phase 7) are not built.
- S3/MinIO storage backend, Postgres, Celery/Redis, Docker Compose, Next.js UI. The storage interface and portable schema are ready for them; `CLIPPIE_STORAGE_BACKEND=s3` fails loudly rather than silently using local disk.
- URL import checks the hostname once; yt-dlp redirects are not re-validated.
- No retention/cleanup of source and analysis files yet.
- CS2/PUBG/GTA profiles, automatic game identification, YOLO.

## Config
Environment variables with the `CLIPPIE_` prefix (see `.env.example`): data dir, workers, padding before/after, limits, NVENC.
