from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from app.config import config
from app.models.schema import MaterialInfo

ROOT = Path(__file__).resolve().parents[2]


def test_brand_modes_and_image_creation(tmp_path):
    from PIL import Image

    path = tmp_path / "generated.png"
    Image.new("RGB", (80, 40), "orange").save(path)
    with (
        patch.object(
            config,
            "app",
            dict(config.app, minimax_api_key="unit-key", video_source="minimax_video"),
        ),
        patch.object(config, "try_save_config", return_value=True),
        patch("app.services.version_checker.poll_available_update") as version,
        patch(
            "app.services.minimax_media.generate_images",
            return_value=[
                MaterialInfo(provider="minimax_image", url=str(path), duration=0)
            ],
        ) as generate,
    ):
        version.return_value.complete = True
        version.return_value.available_version = None
        app = AppTest.from_file(str(ROOT / "webui/Main.py"), default_timeout=60).run()
        assert not app.exception
        assert any("青智焕新" in x.value for x in app.markdown)
        app.session_state["qingzhi_studio_mode"] = "图片创作 / Images"
        app.run()
        app.button(key="qingzhi_image_generate").click().run()
        assert generate.call_count == 0
        app.text_area(key="qingzhi_image_prompt").set_value("new factory").run()
        app.checkbox(key="qingzhi_image_charge").check().run()
        app.button(key="qingzhi_image_generate").click().run()
        assert generate.call_count == 1
        assert app.session_state["qingzhi_image_results"] == [str(path)]
        app.session_state["qingzhi_studio_mode"] = "视频拼接 / Stitch"
        app.run()
        app.button(key="qingzhi_stitch_start").click().run()
        assert any("2–20" in x.value for x in app.error)
        assert not app.exception
