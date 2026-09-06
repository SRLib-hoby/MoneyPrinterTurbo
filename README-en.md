<div align="center">
<img src="resource/public/qingzhi-logo.png" width="140" alt="Qingzhi Renewal logo" />

# 青智焕新 · Qingzhi Renewal

DeepSeek scripts · MiniMax images and video · Video stitching

[简体中文](README.md) · [Cloudflare deployment](docs/cloudflare.md)
</div>

A content studio based on [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo), preserving its Python, Streamlit, voiceover, subtitles, local Docker, and API workflows.

- **Video:** draft a script with DeepSeek, choose official MiniMax H3, confirm provider charges, then generate clips and compose a narrated video.
- **Images:** generate a still with MiniMax `image-01`, preview and download its PNG.
- **Stitching:** upload 2–20 clips, choose their order and output aspect, then download an H.264/AAC MP4 with original audio preserved. Silent inputs receive silence. Maximum 500 MB and 10 minutes total; no AI key needed.

H3 videos use `MiniMax-H3` through the official v2 API (2K, 4–15 seconds). Images use the separate `image-01` model. The existing Metaso H3 provider remains separate and requires its own key. The narrated-video pipeline follows upstream audio mixing; use the stitching screen when preserving original clip audio is required.

## Run locally

Install Python 3.11+, uv and FFmpeg including ffprobe:

```sh
uv sync --frozen
uv run streamlit run webui/Main.py
```

Open Settings to configure DeepSeek and official MiniMax. DeepSeek defaults to `deepseek-v4-pro`; the model remains configurable. Choose the correct MiniMax international/China region. Server environments can supply `DEEPSEEK_API_KEY` and `MINIMAX_API_KEY`; they are not automatically persisted in config files or images. Saved keys take precedence. Existing installations retain saved preferences: switch providers explicitly when upgrading.

## Deploy on Cloudflare

Requires a Cloudflare paid account with Containers, Node.js 22+, and running Docker:

```sh
cd cloudflare
npm ci
npm run login
npm run secrets
npm run deploy
```

Provide `APP_ACCESS_PASSWORD` (a random password of at least 24 characters), `DEEPSEEK_API_KEY`, and `MINIMAX_API_KEY`. All app routes are password protected for one trusted workspace owner.

**Temporary storage:** container sleep, restart and deployment erase local files, history and settings. Download work and export settings promptly. Provider keys stored in Cloudflare Secrets survive restarts. This edition has no R2 persistence or multi-user isolation. Cloud uploads are limited to 90 MB per file. See [deployment details](docs/cloudflare.md).

## Verify

```sh
uv run python -m pytest -q test
cd cloudflare
npm run check
npm run dry-run
```

Provider tests use mocks and incur no generation fees. Stitching tests exercise real FFmpeg video and audio. Live model access still requires an eligible key and balance.

MIT licensed; see [LICENSE](LICENSE). Original media processing is from MoneyPrinterTurbo. Official API sources: [MiniMax H3](https://github.com/MiniMax-AI/MiniMax-H3), [MiniMax CLI](https://github.com/MiniMax-AI/cli), and [DeepSeek harness](https://github.com/deepseek-ai/deepseek-harness).
