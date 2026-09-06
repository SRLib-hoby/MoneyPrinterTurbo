import base64
import io
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from app.config import config
from app.models.schema import VideoAspect, MaterialInfo
from app.services import minimax_media as media, material, llm


def response(body, status=200):
    return Mock(status_code=status, json=Mock(return_value=body))


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "app", {"minimax_api_key": "unit-secret"})
    monkeypatch.setattr(config, "proxy", {})
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)


def test_h3_create_poll_success_uses_official_v2_and_same_task():
    with (
        patch.object(
            media.requests, "post", return_value=response({"task_id": "t1"})
        ) as post,
        patch.object(
            media.requests,
            "get",
            side_effect=[
                response({"task": {"status": "running"}}),
                response(
                    {
                        "task": {
                            "status": "succeeded",
                            "content": {"url": "https://cdn.example/result.mp4"},
                            "duration": 6,
                        }
                    }
                ),
            ],
        ) as get,
        patch.object(media.time, "sleep") as sleep,
    ):
        result = media.generate_videos("factory sparks", 6, VideoAspect.landscape)
    assert post.call_count == 1
    assert post.call_args.args[0] == "https://api.minimax.io/v2/video_generation"
    assert post.call_args.kwargs["json"] == {
        "model": "MiniMax-H3",
        "content": [{"type": "text", "text": "factory sparks"}],
        "resolution": "2K",
        "duration": 6,
        "ratio": "16:9",
    }
    assert all(
        c.args[0].endswith("/v2/query/video_generation/t1") for c in get.call_args_list
    )
    sleep.assert_called_once_with(10.0)
    assert result[0].url.endswith("result.mp4")


def test_uncertain_paid_submission_is_never_retried():
    with patch.object(
        media.requests, "post", side_effect=media.requests.Timeout("unit-secret")
    ) as post:
        with pytest.raises(media.MiniMaxUnconfirmedTaskError) as caught:
            media.generate_videos("test scene", 6)
    assert post.call_count == 1
    assert "unit-secret" not in str(caught.value)


@pytest.mark.parametrize("status", ["failed", "cancelled", "expired", "mystery"])
def test_remote_terminal_failure_retains_id(status):
    with (
        patch.object(media.requests, "post", return_value=response({"task_id": "t2"})),
        patch.object(
            media.requests,
            "get",
            return_value=response(
                {"task": {"status": status, "error": {"message": "unit-secret"}}}
            ),
        ),
    ):
        with pytest.raises(media.MiniMaxMediaError) as caught:
            media.generate_videos("test scene", 6)
    assert caught.value.task_id == "t2"
    assert "unit-secret" not in str(caught.value)


def test_image_base64_saves_real_png(tmp_path):
    data = io.BytesIO()
    Image.new("RGB", (64, 32), "orange").save(data, format="PNG")
    body = {
        "base_resp": {"status_code": 0},
        "data": {
            "image_base64": [base64.b64encode(data.getvalue()).decode()],
            "task_id": "img1",
        },
    }
    with patch.object(media.requests, "post", return_value=response(body)) as post:
        items = media.generate_images(
            "new factory", VideoAspect.landscape, str(tmp_path)
        )
    assert post.call_args.args[0].endswith("/v1/image_generation")
    assert post.call_args.kwargs["json"]["model"] == "image-01"
    assert Image.open(items[0].url).size == (64, 32)


def test_http200_provider_error_is_not_success():
    with patch.object(
        media.requests,
        "post",
        return_value=response(
            {"base_resp": {"status_code": 2013, "status_msg": "bad key unit-secret"}}
        ),
    ) as post:
        with pytest.raises(media.MiniMaxMediaError) as caught:
            media.generate_images("factory")
    assert post.call_count == 1 and "unit-secret" not in str(caught.value)


def test_pipeline_stops_after_required_coverage_and_does_not_fallback(tmp_path):
    item = MaterialInfo(
        provider="minimax_video", url="https://cdn.example/video.mp4", duration=6
    )
    with (
        patch.object(media, "generate_videos", return_value=[item]) as generate,
        patch.object(
            material,
            "_save_generated_video_with_retry",
            return_value=str(tmp_path / "clip.mp4"),
        ),
        patch.object(material, "_persist_material_sources"),
    ):
        result = material._download_minimax_media_on_demand(
            task_id="local",
            search_terms=["one", "two", "three"],
            video_aspect=VideoAspect.landscape,
            audio_duration=5,
            max_clip_duration=6,
            material_directory=str(tmp_path),
            source="minimax_video",
        )
    assert len(result) == 1 and generate.call_count == 1
    with (
        patch.object(
            media,
            "generate_videos",
            side_effect=media.MiniMaxUnconfirmedTaskError("unknown"),
        ) as generate,
        patch.object(material, "_persist_material_sources"),
    ):
        with pytest.raises(media.MiniMaxUnconfirmedTaskError):
            material._download_minimax_media_on_demand(
                task_id="local",
                search_terms=["one", "two"],
                video_aspect=VideoAspect.landscape,
                audio_duration=10,
                max_clip_duration=6,
                material_directory=str(tmp_path),
                source="minimax_video",
            )
    assert generate.call_count == 1


def test_deepseek_server_secret_used_without_persisting(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-server-secret")
    settings = {"llm_provider": "deepseek"}
    client = Mock()
    client.chat.completions.create.return_value = llm.ChatCompletion(
        id="test",
        created=0,
        model="deepseek-v4-pro",
        object="chat.completion",
        choices=[
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "script result"},
            }
        ],
    )
    with patch.object(llm, "OpenAI", return_value=client) as constructor:
        result = llm._generate_response("write a script", app_config=settings)
    assert result == "script result"
    assert constructor.call_args.kwargs["api_key"] == "deepseek-server-secret"
    assert "deepseek_api_key" not in settings


def test_paid_task_checkpoint_precedes_polling_and_failure_retains_id():
    checkpoint = Mock(side_effect=RuntimeError("storage offline"))
    with (
        patch.object(media.requests, "post", return_value=response({"task_id": "paid-1"})) as post,
        patch.object(media.requests, "get") as get,
    ):
        with pytest.raises(media.MiniMaxUnconfirmedTaskError) as caught:
            media.generate_videos("scene", 6, on_submitted=checkpoint)
    checkpoint.assert_called_once_with("paid-1")
    assert caught.value.task_id == "paid-1"
    post.assert_called_once()
    get.assert_not_called()
