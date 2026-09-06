import json
import shutil
import subprocess

import numpy as np
import pytest

from app.services.stitching import stitch_videos


def test_invalid_inputs(tmp_path):
    with pytest.raises(ValueError):
        stitch_videos([], tmp_path / "out.mp4")


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="requires ffmpeg and ffprobe",
)
def test_real_mixed_size_audio_and_silent_clips(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    first, second, output = [
        tmp_path / name for name in ["red.mp4", "blue.mp4", "stitched.mp4"]
    ]
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=24:d=0.8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(first),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=90x160:r=25:d=0.8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(second),
        ],
        check=True,
    )
    stitch_videos([first, second], output, "16:9")
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ]
        )
    )
    assert {s["codec_type"] for s in info["streams"]} == {"audio", "video"}
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1280, 720)
    assert 1.5 < float(info["format"]["duration"]) < 1.85
    colors = []
    for timestamp in ["0.3", "1.2"]:
        raw = subprocess.check_output(
            [
                ffmpeg,
                "-v",
                "error",
                "-ss",
                timestamp,
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-vf",
                "crop=100:100",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ]
        )
        colors.append(np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).mean(axis=0))
    assert colors[0][0] > 200 and colors[1][2] > 200
    audio = subprocess.check_output(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(output),
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "48000",
            "pipe:1",
        ]
    )
    samples = np.frombuffer(audio, dtype=np.float32)
    assert np.sqrt(np.mean(samples[12000:24000] ** 2)) > 0.01
    assert np.max(np.abs(samples[57600:67200])) < 0.005
