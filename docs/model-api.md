# 官方模型 API 核实 / Verified model API contracts

Checked 2026-09-06 via GitHub CLI reading official repositories. No credentials read and no paid generation requested. gstack browse was attempted first for three official sites; local DNS resolves docs hosts to IPv6 ULA `fdfe:dcba:9876::30` and synthetic IPv4 `198.18.0.48`. The browse metadata-address guard rejects ULA IPv6, so no browser safety settings were changed. Official GitHub source remained accessible.

## Decisions / 实现结论

- Hailuo 3 is available as **`MiniMax-H3`**, using the **v2** video API, verified by both official H3 release examples and current CLI implementation.
- Still-image generation is a separate MiniMax **`image-01`** endpoint. Do not label still-image generation as an H3 capability.
- Current DeepSeek official harness defaults to **`deepseek-v4-flash`**, **`deepseek-v4-pro`** (and vision-exp), not exclusively the older `deepseek-chat` name. Keep model selectable/configurable.

## MiniMax H3

Base URL: `https://api.minimax.io` (global), `https://api.minimaxi.com` (China). Headers: `Authorization: Bearer <key>`, `Content-Type: application/json`.

```json
POST /v2/video_generation
{
  "model": "MiniMax-H3",
  "content": [{"type":"text","text":"A concrete video scene and sound description"}],
  "resolution": "2K",
  "duration": 6,
  "ratio": "16:9"
}
```

Create response: `{"task_id":"..."}`.

Poll `GET /v2/query/video_generation/{task_id}`. Response:

```json
{
  "task": {
    "id": "...",
    "model": "MiniMax-H3",
    "status": "succeeded",
    "content": {"url":"https://provider-cdn.example/result.mp4"},
    "duration": 6,
    "resolution":"2K",
    "ratio":"16:9"
  }
}
```

Lifecycle: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `expired`. Failure details: `task.error.code` and `task.error.message`. Successful H3 video is directly downloadable from **`task.content.url`**. Do not send it through the legacy file-id retrieval flow.

Use a polling interval of **at least 10 seconds**: the SDK has a 5-second default, but its more specific H3 operational guide says not to poll more frequently than every 10 seconds. Preserve task ID when a timeout occurs; never automatically submit another paid generation because a poll or download failed.

Validation from official CLI:

- Model exact string `MiniMax-H3`; text nonempty and at most 7,000 characters.
- Duration integer 4–15 seconds. Current official CLI supports `resolution: "2K"` only. H3 repository discusses 768p base-model/API demonstrations separately; use 2K for the validated production path.
- Ratios: `adaptive`, `21:9`, `16:9`, `4:3`, `1:1`, `3:4`, `9:16`. Text-only needs a concrete ratio, not adaptive.
- First-frame item: `{"type":"image_url","image_url":{"url":"..."},"role":"first_frame"}`. `last_frame` is the equivalent last-frame role. At most one of each, ratio adaptive.
- Reference items: image role `reference_image`; video `{"type":"video_url","video_url":{"url":"..."},"role":"reference_video"}`; audio analogous `audio_url` / `reference_audio`.
- Frame mode and reference mode cannot be combined. Audio references require at least one image or video reference.
- Up to 9 reference images, 3 videos, 3 audios; mixed-reference total ≤12. Each video/audio 2–15 seconds, aggregate video ≤15 seconds and aggregate audio ≤15 seconds. Image ≤30 MB, video ≤50 MB, audio ≤15 MB; request body ≤64 MB.
- Use a compatible pay-as-you-go API key. H3 guide excludes OAuth and Token Plan subscription keys. Error 2013 about plan support requires a compatible credential, not prompt changes.
- Native H3 output supports stereo audio. Preserve that audio during concatenation.

Sources, official CLI commit `74e904a19fce4b0660fa66f67ba7ee55ad89ddab`:

- [Endpoint paths](https://github.com/MiniMax-AI/cli/blob/74e904a19fce4b0660fa66f67ba7ee55ad89ddab/src/client/endpoints.ts)
- [Video request validation](https://github.com/MiniMax-AI/cli/blob/74e904a19fce4b0660fa66f67ba7ee55ad89ddab/src/video/v2.ts)
- [Request and response types](https://github.com/MiniMax-AI/cli/blob/74e904a19fce4b0660fa66f67ba7ee55ad89ddab/src/types/api.ts)
- [Video SDK polling](https://github.com/MiniMax-AI/cli/blob/74e904a19fce4b0660fa66f67ba7ee55ad89ddab/src/sdk/video/index.ts)
- [H3 operational and media rules](https://github.com/MiniMax-AI/cli/blob/74e904a19fce4b0660fa66f67ba7ee55ad89ddab/skill/h3-video/references/h3-video.md)
- [Official H3 direct-create/poll/download example](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/scripts/readme/full-2k-t2va-reference-2k-result-by-directly-calling-open-platform-api.sh)
- [Official H3 release](https://github.com/MiniMax-AI/MiniMax-H3)
- [Official docs linked from release](https://platform.minimax.io/docs/api-reference/video-generation-v2-create) (link verified in README; webpage itself could not be opened).

## MiniMax images / 图片

```json
POST /v1/image_generation
{
  "model": "image-01",
  "prompt": "A clear scene description",
  "aspect_ratio": "16:9",
  "n": 1,
  "response_format": "url",
  "prompt_optimizer": true
}
```

Synchronous response shape:

```json
{
  "base_resp": {"status_code":0,"status_msg":"success"},
  "data": {
    "image_urls":["https://provider-cdn.example/image.jpg"],
    "task_id":"...",
    "success_count":1,
    "failed_count":0
  }
}
```

`response_format: "base64"` instead returns `data.image_base64: string[]`. Always detect `base_resp.status_code != 0`, even after HTTP 200. `width` and `height`, if used, must both be present, each 512–2048 and divisible by 8; omit aspect ratio when providing dimensions. Other official request fields: `seed`, `aigc_watermark`, `subject_reference:[{type,image_url?,image_file?}]`. Count limits were not independently verified; conservative n=1 is appropriate.

- [Official image SDK](https://github.com/MiniMax-AI/cli/blob/74e904a19fce4b0660fa66f67ba7ee55ad89ddab/src/sdk/image/index.ts)
- [Official image types](https://github.com/MiniMax-AI/cli/blob/74e904a19fce4b0660fa66f67ba7ee55ad89ddab/src/types/api.ts)
- [Official regional hosts](https://github.com/MiniMax-AI/cli/blob/74e904a19fce4b0660fa66f67ba7ee55ad89ddab/src/config/schema.ts)

## DeepSeek script generation / 脚本

Verified in current official `deepseek-ai/deepseek-harness` master:

- Public base `https://api.deepseek.com`.
- `POST /chat/completions` with Bearer Authorization and JSON content type.
- OpenAI-compatible model/messages request, optional max_tokens, temperature, stop.
- Current official catalog: `deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-v4-flash-vision-exp`.
- Disable thinking for ordinary script generation with `thinking:{"type":"disabled"}` if using the V4 route. Reasoning-enabled requests use `thinking:{"type":"enabled"}` plus `reasoning_effort` of low/high/max.
- Harness uses `stream:true`, `stream_options:{include_usage:true}` and SSE. Its source verifies the standard chat route, not the nonstreaming response-format option. Do not claim JSON mode was freshly verified: current harness does not use `response_format`; structured script output can be prompted and validated in the application.
- Existing project can retain its OpenAI-compatible client and configurable model rather than introduce the large official harness dependency.

Sources:

- [Models and public endpoint](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek/src/index.ts)
- [Authenticated chat transport](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek/src/adapter.ts)
- [Wire serialization](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek/src/serialize.ts)

## Limits of this verification / 核实边界

Protocol and current model names are verified against official current source. No live generation, billing entitlement, regional account access, CDN CORS support, or service latency was tested. Application configuration and deployment should explain required server-side keys, and report provider errors without fabricating generated outputs.
