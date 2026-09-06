"""Focused image and stitching screens for the Qingzhi workspace."""

from pathlib import Path
from uuid import uuid4

import streamlit as st

from app.config import config
from app.models.schema import VideoAspect
from app.services import minimax_media, stitching
from app.utils import utils


def _folder(kind):
    folder = Path(utils.storage_dir()) / "studio" / kind / str(uuid4())
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def render_image_studio():
    st.subheader("图片创作 / Image studio")
    st.caption(
        "使用 MiniMax image-01 生成可下载的图片。API 密钥在顶部设置中配置。 / Create downloadable images with MiniMax image-01. Configure the API key in Settings."
    )
    prompt = st.text_area(
        "画面描述 / Image prompt",
        max_chars=1500,
        height=140,
        key="qingzhi_image_prompt",
    )
    aspect = st.selectbox(
        "图片画幅 / Image aspect", ["16:9", "9:16", "1:1"], key="qingzhi_image_aspect"
    )
    confirmed = st.checkbox(
        "同意生成 1 张图片的 MiniMax 费用 / Confirm MiniMax charges for 1 image",
        key="qingzhi_image_charge",
    )
    if st.button(
        "生成图片 / Generate image", type="primary", key="qingzhi_image_generate"
    ):
        st.session_state.pop("qingzhi_image_results", None)
        if not prompt.strip():
            st.error("请填写画面描述 / Enter an image prompt")
        elif not confirmed:
            st.error("请确认图片生成费用 / Confirm image generation charges")
        elif not minimax_media.is_enabled(
            config.snapshot_config_with_pending(config.app)
        ):
            st.error(
                "请在设置中填写 MiniMax API Key / Configure the MiniMax API key in Settings"
            )
        else:
            with config.try_runtime_config_lock() as acquired:
                if not acquired:
                    st.warning(
                        "已有视频任务正在使用模型配置，请完成后重试 / A video task is using this configuration; retry after it finishes"
                    )
                else:
                    try:
                        with st.spinner("正在生成图片 / Generating image…"):
                            items = minimax_media.generate_images(
                                prompt.strip(),
                                VideoAspect(aspect),
                                str(_folder("images")),
                            )
                        st.session_state["qingzhi_image_results"] = [
                            item.url for item in items
                        ]
                    except minimax_media.MiniMaxMediaError as exc:
                        st.error(str(exc))
    for index, raw in enumerate(st.session_state.get("qingzhi_image_results", [])):
        path = Path(raw)
        if path.is_file():
            st.image(str(path), width=640)
            st.download_button(
                "下载图片 / Download image",
                path.read_bytes(),
                file_name="qingzhi-image.png",
                mime="image/png",
                key=f"qingzhi_image_download_{index}",
            )
        else:
            st.info(
                "临时图片已清理，请使用已下载的副本 / Temporary image no longer available; use your downloaded copy"
            )


def render_stitch_studio():
    st.subheader("视频拼接 / Video stitching")
    st.caption(
        "上传视频，按列表顺序拼接，保留原声；无音轨的片段补静音。最多 20 个文件、合计 500 MB、10 分钟。 / Join up to 20 videos in order, preserving original audio and adding silence where needed. Maximum 500 MB and 10 minutes total."
    )
    files = st.file_uploader(
        "上传视频 / Upload videos",
        type=["mp4", "mov", "mkv", "webm", "avi"],
        accept_multiple_files=True,
        key="qingzhi_stitch_files",
    )
    labels = [f"{i + 1}. {file.name}" for i, file in enumerate(files)]
    # A changed upload set resets the ordering; deliberate reorder survives reruns.
    signature = [(f.name, f.size, getattr(f, "file_id", "")) for f in files]
    if st.session_state.get("qingzhi_stitch_signature") != signature:
        st.session_state["qingzhi_stitch_signature"] = signature
        st.session_state["qingzhi_stitch_order"] = labels
    order = st.multiselect(
        "拼接顺序 / Clip order",
        labels,
        key="qingzhi_stitch_order",
        help="移除后按想要的顺序重新选择。 / Remove and reselect clips in the desired order.",
    )
    aspect = st.selectbox(
        "成片画幅 / Output aspect", list(stitching.SIZES), key="qingzhi_stitch_aspect"
    )
    if st.button(
        "开始拼接 / Stitch videos", type="primary", key="qingzhi_stitch_start"
    ):
        st.session_state.pop("qingzhi_stitch_result", None)
        chosen = [files[labels.index(label)] for label in order]
        if not 2 <= len(chosen) <= stitching.MAX_CLIPS:
            st.error("请选择 2–20 个视频 / Select 2–20 videos")
        elif sum(f.size for f in chosen) > stitching.MAX_TOTAL_BYTES:
            st.error("总大小不能超过 500 MB / Total input size exceeds 500 MB")
        else:
            folder = _folder("stitches")
            paths = []
            try:
                for index, file in enumerate(chosen):
                    path = folder / f"input-{index}{Path(file.name).suffix.lower()}"
                    path.write_bytes(file.getbuffer())
                    paths.append(str(path))
                with st.spinner(
                    "正在统一画幅并拼接视频 / Normalizing and stitching videos…"
                ):
                    result = stitching.stitch_videos(
                        paths, str(folder / "qingzhi-stitched.mp4"), aspect
                    )
                st.session_state["qingzhi_stitch_result"] = result
                st.success("拼接完成 / Stitching complete")
            except (ValueError, OSError) as exc:
                st.error(str(exc))
            finally:
                for path in paths:
                    Path(path).unlink(missing_ok=True)
    result = st.session_state.get("qingzhi_stitch_result", "")
    if result and Path(result).is_file():
        st.video(result)
        st.download_button(
            "下载成片 / Download video",
            Path(result).read_bytes(),
            file_name="qingzhi-stitched.mp4",
            mime="video/mp4",
            key="qingzhi_stitch_download",
        )
