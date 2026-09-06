"""Official MiniMax H3 video and image-01 image generation.

Contracts: MiniMax-AI/cli src/video/v2.ts, src/sdk/image/index.ts and
src/types/api.ts. Paid submissions are never retried automatically.
"""

from __future__ import annotations

import base64
import math
import os
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

import requests
from loguru import logger

from app.config import config
from app.models.schema import MaterialInfo, VideoAspect


DEFAULT_BASE_URL = "https://api.minimax.io"
DEFAULT_IMAGE_MODEL_ID = "image-01"
DEFAULT_MODEL_ID = "MiniMax-H3"
DEFAULT_RESOLUTION = "2K"
DEFAULT_MIN_DURATION_SECONDS = 4
DEFAULT_MAX_DURATION_SECONDS = 15
DEFAULT_POLL_INTERVAL_SECONDS = 10.0
DEFAULT_RUN_TIMEOUT_SECONDS = 1800.0
MAX_PROMPT_LENGTH = 7000
MAX_POLL_RETRIES = 5
RETRY_BASE_SECONDS = 1.0
MAX_ERROR_TEXT_LENGTH = 500
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_FAILURE_STATUSES = frozenset({"failed", "cancelled", "canceled", "expired"})
SUPPORTED_RESOLUTIONS = frozenset({"2K"})


class MiniMaxMediaError(RuntimeError):
    """A definitive configuration, request or result failure."""

    def __init__(self, message: str, task_id: str = ""):
        super().__init__(message)
        # 远端任务一旦创建，所有后续异常都携带同一个 ID。任务服务可以统一
        # 保存恢复线索，不需要了解轮询、结果解析或下载分别在哪一步失败。
        self.task_id = task_id


class MiniMaxUnconfirmedTaskError(MiniMaxMediaError):
    """远端可能已创建付费任务，但本机无法确认其最终状态。"""


class MiniMaxDownloadError(MiniMaxMediaError):
    """远端付费任务已成功，但成片未能下载到本机。"""


def get_api_key(settings: Mapping[str, Any] | None = None) -> str:
    """Use the official MiniMax configuration key, then its environment key."""
    settings = config.app if settings is None else settings
    configured = str(
        settings.get("minimax_media_api_key", "")
        or settings.get("minimax_api_key", "")
        or ""
    ).strip()
    environment_key = os.getenv("MINIMAX_API_KEY", "").strip()
    return configured or environment_key


def is_enabled(settings: Mapping[str, Any] | None = None) -> bool:
    """Return whether official MiniMax credentials are configured."""
    return bool(get_api_key(settings))


def _base_url() -> str:
    value = (
        str(
            config.app.get("minimax_media_base_url", DEFAULT_BASE_URL)
            or DEFAULT_BASE_URL
        )
        .strip()
        .rstrip("/")
    )
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"api.minimax.io", "api.minimaxi.com"}
        or parsed.path not in {"", "/v1"}
        or parsed.query
        or parsed.fragment
    ):
        raise MiniMaxMediaError(
            "minimax_media_base_url must be https://api.minimax.io or "
            "https://api.minimaxi.com (optional /v1)"
        )
    return f"https://{parsed.netloc}"


def _model(config_key: str, default: str) -> str:
    value = str(config.app.get(config_key, default) or default).strip()
    if value != default:
        raise MiniMaxMediaError(f"{config_key} currently supports {default} only")
    return value


def _resolution() -> str:
    configured = config.app.get("minimax_resolution", DEFAULT_RESOLUTION)
    value = str(configured).strip().upper()
    if value not in SUPPORTED_RESOLUTIONS:
        supported = ", ".join(sorted(SUPPORTED_RESOLUTIONS))
        # 分辨率直接影响生成费用，用户显式写错时不能静默退回 2K。只有配置项
        # 完全缺失时才使用默认值，避免无意间创建比预期更贵的任务。
        raise MiniMaxMediaError(
            f"Unsupported MiniMax resolution {value!r}; expected one of: {supported}"
        )
    return value


def _tls_verify() -> bool:
    value = config.app.get("tls_verify", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _bounded_float(key: str, default: float, minimum: float, maximum: float) -> float:
    """读取有限浮点配置，并限制在不会压垮远端或本机的安全范围内。"""
    try:
        value = float(config.app.get(key, default))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, minimum), maximum)


def _status_code(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        return 200


def _redact_secret(value: Any, api_key: str) -> str:
    """保留可排障文本，同时移除 API Key、URL 编码 Key 和代理凭据。"""
    text = str(value or "")
    if api_key:
        text = text.replace(api_key, "***")
        encoded = quote_plus(api_key)
        if encoded != api_key:
            text = text.replace(encoded, "***")
    for proxy_url in config.proxy.values():
        proxy_secret = str(proxy_url or "")
        if proxy_secret:
            text = text.replace(proxy_secret, "***")
    return text[:MAX_ERROR_TEXT_LENGTH]


def _response_error(response: Any, api_key: str) -> str:
    """兼容 MiniMax V2 的嵌套错误结构，并限制日志中的响应长度。"""
    try:
        payload = response.json()
    except Exception:
        return f"HTTP {_status_code(response)}"
    if not isinstance(payload, dict):
        return f"HTTP {_status_code(response)}"

    error = payload.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        message = error.get("message")
        http_code = error.get("http_code")
    else:
        error_type = None
        message = payload.get("message") or error
        http_code = payload.get("code")
    detail = ": ".join(
        str(item) for item in (error_type, http_code, message) if item not in (None, "")
    )
    return _redact_secret(detail or f"HTTP {_status_code(response)}", api_key)


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(
        error,
        (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
        ),
    ):
        return True
    response = getattr(error, "response", None)
    return response is not None and _status_code(response) in RETRYABLE_STATUS_CODES


def _normalize_duration(minimum_duration: int) -> tuple[int, int]:
    """返回“用户请求时长、实际提交时长”，用于日志解释最短 4 秒约束。"""
    try:
        requested = int(minimum_duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MiniMaxMediaError(
            "MiniMax clip duration must be a positive integer"
        ) from exc
    if (
        isinstance(minimum_duration, bool)
        or requested != minimum_duration
        or requested <= 0
    ):
        raise MiniMaxMediaError("MiniMax clip duration must be a positive integer")
    duration = min(
        max(requested, DEFAULT_MIN_DURATION_SECONDS),
        DEFAULT_MAX_DURATION_SECONDS,
    )
    return requested, duration


def generate_videos(
    search_term: str,
    minimum_duration: int,
    video_aspect: VideoAspect = VideoAspect.portrait,
    on_submitted=None,
) -> list[MaterialInfo]:
    """Submit one official H3 task and return its downloadable result."""
    api_key = get_api_key()
    if not api_key:
        raise MiniMaxMediaError("MiniMax requires an API key")

    term = str(search_term or "").strip()
    if not term:
        # 空提示词通常表示上游脚本拆分失败。付费接口不能用无效输入试探，
        # 否则即使远端接受也只会产生无法使用的计费素材。
        raise MiniMaxMediaError("MiniMax search term must not be empty")
    if len(term) > MAX_PROMPT_LENGTH:
        raise MiniMaxMediaError(
            f"MiniMax search term exceeds {MAX_PROMPT_LENGTH} characters"
        )

    try:
        aspect = VideoAspect(video_aspect)
    except ValueError as exc:
        raise MiniMaxMediaError("Unsupported MiniMax aspect ratio") from exc
    requested_duration, duration = _normalize_duration(minimum_duration)
    if duration != requested_duration:
        logger.info(
            "MiniMax clip duration adjusted to H3 limits: "
            f"requested={requested_duration}s, using={duration}s, "
            f"supported={DEFAULT_MIN_DURATION_SECONDS}-{DEFAULT_MAX_DURATION_SECONDS}s"
        )
    resolution = _resolution()
    payload = {
        "model": _model("minimax_video_model", DEFAULT_MODEL_ID),
        "content": [{"type": "text", "text": term}],
        "resolution": resolution,
        "duration": duration,
        "ratio": aspect.value,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base_url = _base_url()
    create_url = f"{base_url}/v2/video_generation"
    logger.info(
        "generating video with MiniMax H3: "
        f"resolution={resolution}, ratio={aspect.value}, duration={duration}s, "
        f"prompt_length={len(term)}"
    )

    # POST 超时或 5xx 发生时，远端可能已经创建并计费。接口没有提供客户端
    # 幂等键，因此这里绝不自动重发；上层会停止后续关键词，避免重复扣费。
    try:
        response = requests.post(
            create_url,
            json=payload,
            headers=headers,
            proxies=config.proxy,
            verify=_tls_verify(),
            timeout=(30, 60),
        )
    except Exception as exc:
        raise MiniMaxUnconfirmedTaskError(
            "MiniMax submission returned no response; a paid task may "
            "already exist remotely: "
            f"error={type(exc).__name__}, detail={_redact_secret(exc, api_key)}"
        ) from exc

    status_code = _status_code(response)
    if status_code >= 500:
        raise MiniMaxUnconfirmedTaskError(
            f"MiniMax submission failed with HTTP {status_code}; a paid "
            "task may already exist remotely"
        )
    if not 200 <= status_code < 300:
        raise MiniMaxMediaError(
            "MiniMax video generation request rejected: "
            f"HTTP {status_code}, {_response_error(response, api_key)}"
        )
    try:
        body = response.json()
    except Exception as exc:
        raise MiniMaxUnconfirmedTaskError(
            "MiniMax submission returned an unreadable response; a paid "
            f"task may already exist remotely: error={type(exc).__name__}"
        ) from exc

    task_id = str(body.get("task_id") or "").strip() if isinstance(body, dict) else ""
    if not task_id:
        raise MiniMaxUnconfirmedTaskError(
            "MiniMax accepted the submission without returning a task id"
        )
    logger.info(f"MiniMax paid task created: id={task_id}")
    if on_submitted is not None:
        try:
            on_submitted(task_id)
        except Exception:
            raise MiniMaxUnconfirmedTaskError(
                f"MiniMax task submitted but checkpoint failed; do not resubmit: id={task_id}",
                task_id=task_id,
            ) from None

    task = _wait_for_task(
        task_id=task_id,
        base_url=base_url,
        headers=headers,
        api_key=api_key,
    )
    content = task.get("content")
    video_url = content.get("url") if isinstance(content, dict) else None
    if not _is_media_url(video_url):
        raise MiniMaxMediaError(
            f"MiniMax task succeeded without a downloadable video: id={task_id}",
            task_id=task_id,
        )

    actual_duration = task.get("duration", duration)
    try:
        actual_duration = int(actual_duration)
    except (TypeError, ValueError, OverflowError):
        actual_duration = duration
    if actual_duration <= 0:
        actual_duration = duration

    return [
        MaterialInfo(
            provider="minimax_video",
            url=video_url,
            duration=actual_duration,
            source_info={
                "provider": "minimax_video",
                "search_term": term,
                "asset_id": task_id,
                # MiniMax 2K 是规格名称，接口没有承诺固定像素尺寸。
                # 不猜测 width/height，后续若需要精确尺寸应以下载文件探测值为准。
                "rendition": {"id": task_id},
            },
        )
    ]


def _wait_for_task(
    *,
    task_id: str,
    base_url: str,
    headers: dict[str, str],
    api_key: str,
) -> dict[str, Any]:
    """轮询同一个付费任务，直到成功、明确失败或本地无法确认状态。"""
    deadline = time.monotonic() + _bounded_float(
        "minimax_run_timeout",
        DEFAULT_RUN_TIMEOUT_SECONDS,
        60.0,
        7200.0,
    )
    poll_interval = _bounded_float(
        "minimax_poll_interval",
        DEFAULT_POLL_INTERVAL_SECONDS,
        10.0,
        60.0,
    )
    query_url = f"{base_url}/v2/query/video_generation/{quote(task_id, safe='')}"
    consecutive_failures = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MiniMaxUnconfirmedTaskError(
                "MiniMax task is still running after the configured local "
                f"wait timeout: id={task_id}",
                task_id=task_id,
            )

        # connect/read timeout 分别计时，均使用剩余时间的一半，保证一次 GET
        # 不会有意越过任务总截止时间。到期后不会再发起下一次轮询。
        phase_timeout = max(min(remaining / 2.0, 30.0), 0.001)
        try:
            response = requests.get(
                query_url,
                headers=headers,
                proxies=config.proxy,
                verify=_tls_verify(),
                timeout=(phase_timeout, phase_timeout),
            )
            status_code = _status_code(response)
            if status_code in RETRYABLE_STATUS_CODES:
                raise requests.exceptions.HTTPError(
                    f"HTTP {status_code}", response=response
                )
            if not 200 <= status_code < 300:
                raise MiniMaxUnconfirmedTaskError(
                    "MiniMax task status is unknown: "
                    f"http_status={status_code}, "
                    f"detail={_response_error(response, api_key)}",
                    task_id=task_id,
                )
            body = response.json()
            task = body.get("task") if isinstance(body, dict) else None
            if not isinstance(task, dict):
                raise MiniMaxUnconfirmedTaskError(
                    "MiniMax task status response is malformed",
                    task_id=task_id,
                )
        except MiniMaxUnconfirmedTaskError:
            raise
        except Exception as exc:
            if not _is_retryable_error(exc):
                raise MiniMaxUnconfirmedTaskError(
                    "MiniMax polling failed and the paid task state is "
                    f"unknown: error={type(exc).__name__}, "
                    f"detail={_redact_secret(exc, api_key)}",
                    task_id=task_id,
                ) from exc

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MiniMaxUnconfirmedTaskError(
                    "MiniMax task is still running after the configured "
                    f"local wait timeout: id={task_id}",
                    task_id=task_id,
                ) from exc
            consecutive_failures += 1
            if consecutive_failures > MAX_POLL_RETRIES:
                raise MiniMaxUnconfirmedTaskError(
                    "MiniMax polling failed after retries; the paid task "
                    f"may still be running remotely: id={task_id}",
                    task_id=task_id,
                ) from exc
            delay = min(
                max(poll_interval, RETRY_BASE_SECONDS * consecutive_failures), remaining
            )
            logger.warning(
                "MiniMax polling hit a transient error; retrying the same "
                f"task: id={task_id}, attempt={consecutive_failures}/"
                f"{MAX_POLL_RETRIES}, retry_in={delay:.1f}s"
            )
            time.sleep(delay)
            continue

        consecutive_failures = 0
        status = str(task.get("status") or "").strip().lower()
        logger.info(f"MiniMax task status: id={task_id}, status={status}")
        if status == "succeeded":
            return task
        if status in TERMINAL_FAILURE_STATUSES:
            raise MiniMaxMediaError(
                "MiniMax task did not produce a video: "
                f"id={task_id}, status={status}, "
                f"detail={_redact_secret(task.get('error'), api_key)}",
                task_id=task_id,
            )
        if status not in ACTIVE_STATUSES:
            raise MiniMaxUnconfirmedTaskError(
                "MiniMax returned an unknown task status: "
                f"id={task_id}, status={status!r}",
                task_id=task_id,
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MiniMaxUnconfirmedTaskError(
                "MiniMax task is still running after the configured local "
                f"wait timeout: id={task_id}",
                task_id=task_id,
            )
        time.sleep(min(poll_interval, remaining))


def _is_media_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def generate_images(
    prompt: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
    save_dir: str = "",
) -> list[MaterialInfo]:
    """Generate one official image-01 image, save a PNG and retain dimensions.

    URL retrieval may retry the same paid result. The generation POST is issued
    only once, including when HTTP succeeds but the response cannot be read.
    """
    # Import lazily: material owns shared download, image decode and PNG helpers,
    # and also dispatches to this provider from its on-demand video pipeline.
    from app.services import material

    api_key = get_api_key()
    if not api_key:
        raise MiniMaxMediaError("MiniMax requires an API key")
    term = str(prompt or "").strip()
    if not term or len(term) > 1500:
        raise MiniMaxMediaError("MiniMax image prompt must contain 1–1500 characters")
    try:
        aspect = VideoAspect(video_aspect)
    except ValueError as exc:
        raise MiniMaxMediaError("Unsupported MiniMax aspect ratio") from exc
    payload = {
        "model": _model("minimax_image_model", DEFAULT_IMAGE_MODEL_ID),
        "prompt": term,
        "aspect_ratio": aspect.value,
        "n": 1,
        "response_format": "url",
        "prompt_optimizer": True,
    }
    endpoint = f"{_base_url()}/v1/image_generation"
    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            proxies=config.proxy,
            verify=_tls_verify(),
            timeout=(30, 300),
        )
    except Exception as exc:
        raise MiniMaxUnconfirmedTaskError(
            "MiniMax image submission returned no response; a paid request may "
            f"already exist remotely: error={type(exc).__name__}"
        ) from exc
    status = _status_code(response)
    if status >= 500:
        raise MiniMaxUnconfirmedTaskError(
            f"MiniMax image submission returned HTTP {status}; a paid request "
            "may already exist remotely"
        )
    if not 200 <= status < 300:
        raise MiniMaxMediaError(
            f"MiniMax image request rejected: HTTP {status}, {_response_error(response, api_key)}"
        )
    try:
        body = response.json()
    except Exception as exc:
        raise MiniMaxUnconfirmedTaskError(
            "MiniMax image response was unreadable; do not automatically resubmit"
        ) from exc
    if not isinstance(body, dict):
        raise MiniMaxUnconfirmedTaskError("MiniMax image response was malformed")
    base = body.get("base_resp")
    data = body.get("data")
    task_id = str(data.get("task_id") or "") if isinstance(data, dict) else ""
    if not isinstance(base, dict) or base.get("status_code") != 0:
        detail = (
            _redact_secret(base.get("status_msg"), api_key)
            if isinstance(base, dict)
            else "missing base_resp"
        )
        raise MiniMaxMediaError(
            f"MiniMax image generation failed: {detail}", task_id=task_id
        )
    if not isinstance(data, dict):
        raise MiniMaxMediaError(
            "MiniMax image response has no image data", task_id=task_id
        )
    urls, encoded = data.get("image_urls"), data.get("image_base64")
    image_bytes = None
    if isinstance(urls, list) and urls and _is_media_url(urls[0]):
        image_bytes, _ = material._openai_image_download_bytes(urls[0], api_key)
        if not image_bytes:
            raise MiniMaxDownloadError(
                "MiniMax generated a paid image but the result could not be downloaded",
                task_id=task_id,
            )
    elif isinstance(encoded, list) and encoded and isinstance(encoded[0], str):
        try:
            image_bytes = base64.b64decode(encoded[0], validate=True)
        except (ValueError, TypeError) as exc:
            raise MiniMaxMediaError(
                "MiniMax returned invalid image base64", task_id=task_id
            ) from exc
    if not image_bytes:
        raise MiniMaxMediaError(
            "MiniMax returned no usable generated image", task_id=task_id
        )
    try:
        image_path, width, height = material._save_openai_image_file(
            image_bytes, save_dir
        )
    except Exception as exc:
        raise MiniMaxDownloadError(
            "MiniMax generated a paid image but it could not be decoded or saved: "
            f"error={type(exc).__name__}",
            task_id=task_id,
        ) from exc
    return [
        MaterialInfo(
            provider="minimax_image",
            url=image_path,
            duration=0,
            source_info={
                "provider": "minimax_image",
                "search_term": term,
                "asset_id": task_id,
                "rendition": {"id": task_id, "width": width, "height": height},
            },
        )
    ]
