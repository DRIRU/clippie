"""OCR smoke benchmark on a SYNTHETIC kill-feed video (not real game footage).

Reports per-frame latency and how often the expected names are read. Treat as a
wiring/speed check only; real accuracy needs your own labelled footage.

    python -m tools.bench_ocr
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fragforge.detection import ocr  # noqa: E402
from fragforge.profiles.valorant import KILLFEED  # noqa: E402
from tests.killfeed_video import H, W, render  # noqa: E402

EXPECT = {"phoenix", "jett", "sova", "reyna"}

if __name__ == "__main__":
    d = Path(tempfile.mkdtemp())
    feed = [(2, 7, "Phoenix", "Jett"), (4, 9, "Phoenix", "Sova"), (6, 11, "Phoenix", "Reyna")]
    v = render(d / "bench.mp4", feed, 12)
    be = ocr.get_backend()
    be.read(__import__("numpy").zeros((64, 64), dtype="uint8"))  # warm-up
    t0 = time.time()
    frames = ocr.scan_region(v, W, H, KILLFEED, 1.0, be)
    dt = time.time() - t0
    seen = {w.lower() for fr in frames for row in fr["rows"] for w, _ in row}
    for fr in frames:
        print(fr["t"], fr["rows"])
    print(f"\nbackend={be.name} frames_ocrd={len(frames)} total={dt:.2f}s")
    print(f"expected names found: {sorted(EXPECT & seen)} / {sorted(EXPECT)}")
