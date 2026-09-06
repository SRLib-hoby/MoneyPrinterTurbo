<div align="center">
<img src="resource/public/qingzhi-logo.png" width="140" alt="青智焕新 Logo" />

# 青智焕新

DeepSeek 创作脚本 · MiniMax 生成图片与视频 · 自动剪辑与视频拼接

[English](README-en.md) · [Cloudflare 快速部署](docs/cloudflare.md)
</div>

基于 [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) 的内容创作工作室，保留原有本地、Docker、API 和配音字幕流程。

## 创作流程

- **视频创作**：输入主题 → 使用 DeepSeek 生成脚本和场景关键词 → 选择 MiniMax H3 官方视频源 → 确认生成费用 → 生成素材并自动合成短视频。也可以选择 MiniMax 图片素材、上传本地素材或使用原有供应商。
- **图片创作**：输入画面描述、选择画幅，使用 MiniMax `image-01` 生成一张图片，预览并下载 PNG。
- **视频拼接**：上传 2–20 个视频，按希望的播放顺序选择，统一画幅后输出 MP4。保留原声，无声片段补静音。无需模型 API Key；总大小最多 500 MB、总时长最多 10 分钟。

**型号区别**：`MiniMax-H3` 使用官方 v2 视频接口，当前实现提供 2K、4–15 秒片段；静态图片使用独立的 `image-01`，不是 H3 图片接口。原有“秘塔 MiniMax H3”仍是独立供应商，不可与官方密钥混用。

自动文案视频流程使用既有配音/配乐合成策略；需要保留源视频原声时，请使用“视频拼接”。

## 本地启动

安装 Python 3.11+、[uv](https://docs.astral.sh/uv/) 和 FFmpeg（包括 ffprobe）。

```sh
uv sync --frozen
uv run streamlit run webui/Main.py
```

首次启动会从 `config.example.toml` 创建 `config.toml`。在右上角“设置”中填写：

1. **大模型设置**：默认 DeepSeek，模型默认为 `deepseek-v4-pro`；可按账户支持情况改为其他 DeepSeek 模型。
2. **素材 API**：填写 MiniMax 官方按量付费 API Key，选择对应的国际或国内服务区域。
3. 点击“测试连接”会发起真实模型请求；生成图片和视频按供应商实际规则收费。

也可以在服务器环境中配置 `DEEPSEEK_API_KEY` 与 `MINIMAX_API_KEY`。环境中的密钥只在服务端读取，不自动写入配置文件或镜像。配置文件中的密钥优先于环境变量。

升级已有安装时不会覆盖原有供应商选择和密钥；请在设置里主动切换到 DeepSeek，并将素材来源切换到 MiniMax 官方。

## Cloudflare 快速部署

使用 **Cloudflare Workers + Containers** 运行现有 Python、Streamlit 和 FFmpeg，普通 Pages/Worker 无法直接运行这套原生媒体处理流程。需要支持 Containers 的 Cloudflare 付费账户、Node.js 22+ 与正在运行的 Docker。

```sh
cd cloudflare
npm ci
npm run login
npm run secrets
npm run deploy
```

部署脚本需要：`APP_ACCESS_PASSWORD`（至少 24 个字符的随机密码）、`DEEPSEEK_API_KEY`、`MINIMAX_API_KEY`。入口受密码保护，默认只供一个可信工作室使用者访问。

**R2 持久化**：Cloudflare 部署将作品和任务记录保存到私有 R2，可在“作品与任务”中跨重启查看、恢复和下载。保存失败可单独重试，不重复生成。重启会将未完成任务标记为中断，不自动重提付费请求。配置需另行导出；仍为单用户工作室。云端每个上传文件限制为 90 MB。

详见 [部署、成本与故障排查](docs/cloudflare.md)。

## 测试

```sh
uv run python -m pytest -q test
cd cloudflare
npm run check
npm run dry-run
```

模型合约测试使用模拟响应，不会产生模型费用。拼接测试使用真实 FFmpeg 验证画幅、顺序、原声和静音。真实生成还需要账户权限及额度。

## 来源与许可

保留上游 MIT [LICENSE](LICENSE)。底层视频、配音和字幕处理源于 MoneyPrinterTurbo；本分支增加青智焕新品牌、官方 MiniMax 图片/视频接入、独立拼接工作室和 Cloudflare 部署。

- [MiniMax 官方 H3](https://github.com/MiniMax-AI/MiniMax-H3)
- [MiniMax 官方 CLI 与接口类型](https://github.com/MiniMax-AI/cli)
- [DeepSeek 官方模型接口实现](https://github.com/deepseek-ai/deepseek-harness)
