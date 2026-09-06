"""Ordered short-video concatenation with normalized picture and original audio."""

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.utils import utils

MAX_CLIPS = 20
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_TOTAL_DURATION_SECONDS = 600
SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)}


def _run(args, timeout):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("视频处理超时 / Video processing timed out") from exc
    if result.returncode:
        raise ValueError(
            "无法读取或处理视频，请检查文件格式 / Could not process the video; check its format"
        )
    return result.stdout


def stitch_videos(paths, output_path, video_aspect="16:9"):
    """Normalize sequentially to bound memory; retain audio and pad silent clips.

    The original output, if present, is untouched until the entire render succeeds.
    Inputs must be local files, never URLs. The returned output is H.264/AAC MP4.
    """
    files = [Path(p).resolve() for p in paths]
    output = Path(output_path).resolve()
    if not 2 <= len(files) <= MAX_CLIPS:
        raise ValueError(f"请选择 2–{MAX_CLIPS} 个视频 / Select 2–{MAX_CLIPS} videos")
    if video_aspect not in SIZES:
        raise ValueError("不支持的画幅 / Unsupported aspect ratio")
    if any(not p.is_file() or p == output for p in files):
        raise ValueError(
            "输入必须是有效的本地视频，输出不能覆盖输入 / Invalid input or output path"
        )
    if sum(p.stat().st_size for p in files) > MAX_TOTAL_BYTES:
        raise ValueError("素材总大小不能超过 500 MB / Inputs exceed 500 MB")
    ffmpeg = utils.get_ffmpeg_binary()
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("需要安装 FFmpeg（含 ffprobe）/ Install FFmpeg with ffprobe")
    metadata = []
    for file in files:
        raw = _run(
            [
                ffprobe,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(file),
            ],
            30,
        )
        try:
            info = json.loads(raw)
            duration = float(info["format"]["duration"])
            streams = info["streams"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("视频时长无效 / Invalid video duration") from exc
        if (
            not math.isfinite(duration)
            or duration <= 0
            or not any(s.get("codec_type") == "video" for s in streams)
        ):
            raise ValueError(
                "素材必须包含有效视频轨道 / A valid video track is required"
            )
        metadata.append(
            (duration, any(s.get("codec_type") == "audio" for s in streams))
        )
    if sum(d for d, _ in metadata) > MAX_TOTAL_DURATION_SECONDS:
        raise ValueError("素材总时长不能超过 10 分钟 / Inputs exceed 10 minutes")
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = SIZES[video_aspect]
    with tempfile.TemporaryDirectory(prefix="stitch-", dir=output.parent) as temp:
        folder = Path(temp)
        for index, (file, (duration, has_audio)) in enumerate(zip(files, metadata)):
            args = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(file),
            ]
            if not has_audio:
                args += [
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                ]
            args += [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0" if has_audio else "1:a:0",
                "-t",
                str(duration),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
                "-af",
                "aresample=48000:async=1:first_pts=0,apad",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-threads",
                "2",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-b:a",
                "160k",
                "-map_metadata",
                "-1",
                str(folder / f"{index}.mp4"),
            ]
            _run(args, 600)
        manifest = folder / "list.txt"
        manifest.write_text("".join(f"file '{i}.mp4'\n" for i in range(len(files))))
        final = folder / "result.mp4"
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(manifest),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(final),
            ],
            180,
        )
        os.replace(final, output)
    return str(output)
