"""Generate synthetic sample videos into sample/ (videos are not committed).

    python -m tools.make_samples
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.killfeed_video import render  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "sample"
    out.mkdir(exist_ok=True)
    fc = ("color=c=gray:s=1280x720:r=30:d=40[v1];testsrc2=s=1280x720:r=30:d=8[v2];color=c=gray:s=1280x720:r=30:d=40[v3];"
          "[v1][v2][v3]concat=n=3:v=1:a=0[v];sine=f=220:d=40,volume=0.02[a1];anoisesrc=d=8:a=0.8:c=white[a2];"
          "sine=f=220:d=40,volume=0.02[a3];[a1][a2][a3]concat=n=3:v=0:a=1[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-ar", "44100", "-pix_fmt", "yuv420p", str(out / "demo.mp4")], check=True)
    render(out / "valorant_synthetic.mp4",
           [(6, 11, "Phoenix", "Jett"), (9, 14, "Phoenix", "Sova"), (11, 16, "Phoenix", "Reyna"), (22, 27, "Omen", "Cypher")],
           34, banners=[(18, 20, "CLUTCH")])
    print("wrote", out)
