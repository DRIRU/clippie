"""Motion analysis: the PyAV (dav1d) path used for AV1 must agree with the ffmpeg pipe."""
import subprocess

import numpy as np
import pytest

from clippie.detection import signals


def _make_av1(path):
    # 4 s black then 4 s white: motion is zero except for one spike at the cut. Skip if this ffmpeg cannot encode AV1.
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=30:duration=4",
           "-f", "lavfi", "-i", "color=c=white:size=320x180:rate=30:duration=4",
           "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libaom-av1", "-cpu-used", "8", "-crf", "30",
           "-b:v", "0", str(path)]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        pytest.skip("this ffmpeg cannot encode AV1")


@pytest.mark.skipif(not signals._has_dav1d(), reason="PyAV without libdav1d")
def test_pyav_matches_ffmpeg(tmp_path):
    src = tmp_path / "clip.mkv"
    _make_av1(src)
    ref = np.array(signals._motion_ffmpeg(src, None, 8.0))
    got = np.array(signals._motion_pyav(src, None, 8.0))
    assert abs(len(ref) - len(got)) <= 1
    n = min(len(ref), len(got))
    # PyAV ignores the container's start offset (a fraction of a second), so the cut may land one sample apart
    assert abs(int(np.argmax(ref)) - 8) <= 1 and abs(int(np.argmax(got)) - 8) <= 1
    assert got.max() > 100 and ref.max() > 100 and np.median(got) < 1


def test_video_features_falls_back_to_ffmpeg(tmp_path, monkeypatch):
    src = tmp_path / "h264.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=4",
                    "-c:v", "libx264", str(src)], check=True)
    monkeypatch.setattr(signals, "_motion_pyav", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(signals, "_has_dav1d", lambda: True)
    out = signals.video_features(src, None, 4.0, codec="av1")      # PyAV path raises, so ffmpeg answers
    assert len(out["motion"]) >= 7
