# 青智焕新：Cloudflare 部署 / Cloudflare deployment

使用一个 Worker 作为受密码保护的入口，一个 Cloudflare Container 运行 Streamlit、Python 和 FFmpeg。全站 HTTP、文件下载及 WebSocket 均需认证。默认一个 4 GiB `standard-1` 容器，60 分钟无活动后休眠。运行中任务依赖容器存活；关闭页面后仍可能因休眠或平台重启而中断。

A password-protected Worker forwards traffic to one Python/FFmpeg container. Authentication covers app assets, downloads and WebSockets. This is a single trusted-owner workspace with shared application configuration, not a multi-tenant service.

## 前提 / Prerequisites

- Cloudflare 付费账户并开通 Containers；它不是纯免费 Pages 站点。运行资源和模型生成分别计费，请在控制台查看当前价格及额度。
- Node.js 22+，运行中的 Docker（macOS 可用 OrbStack 或 Docker Desktop）。首次构建需要下载 Python 媒体依赖，可能较慢。
- DeepSeek API Key，以及 MiniMax **按量付费** API Key（H3 不使用 Token Plan/OAuth 订阅凭据）。

## 部署 / Deploy

在仓库根目录开始 / Start from the repository root:

```sh
cd cloudflare
npm ci
npm run login
npx wrangler whoami
npm run secrets
npm run deploy
```

`npm run secrets` 依次安全询问三个密钥；不要把真实密钥写入代码或聊天：

| Secret | Purpose |
|---|---|
| `APP_ACCESS_PASSWORD` | 随机访问密码，至少 24 个字符 / Random access password, at least 24 characters |
| `DEEPSEEK_API_KEY` | DeepSeek 脚本创作 / Script generation |
| `MINIMAX_API_KEY` | 官方 H3 视频和 image-01 图片 / Official media generation |

首次写 secret 时 Wrangler 可能提示创建 Worker，请按提示创建 `qingzhi-huanxin`。部署完成后访问命令输出的 HTTPS 地址，输入访问密码。设置 → 素材 API 中选择与 MiniMax Key 一致的区域：国际 `api.minimax.io` 或国内 `api.minimaxi.com`。访问密码未设置或短于 24 字符时服务返回 503，不会公开应用。

The first secret write may ask to create the Worker. Visit the HTTPS URL returned by deployment and sign in. Select the MiniMax region matching your account in Settings → Material API. Missing/short access passwords fail closed with HTTP 503.

## 文件和任务 / Files and tasks

- 容器磁盘是临时的；休眠、重启、更新会丢失本地配置、历史和作品。及时下载文件和导出设置；环境密钥仍由 Cloudflare Secrets 保存。
- 此版本没有持久化 R2、任务恢复队列或用户隔离。需要长期保存历史时，先使用本地 Docker 挂载存储，或进一步增加 R2 和任务持久化。
- 云端单文件上传最多 90 MB；独立拼接总计最多 500 MB、20 片段、10 分钟。过大的任务应拆分后处理。
- 不要在生成过程中部署更新。长时间生成时保持页面打开；平台故障仍可能中断任务。
- MiniMax 提交超时可能已产生费用，系统不会自动重新提交。请保留报错中的远端任务 ID，到 MiniMax 控制台核查。

Container disk is ephemeral. Download/export before sleep or redeployment; generation can be interrupted by platform restarts. No persistent artifact/history recovery is promised. Keep the page open during long jobs and do not redeploy mid-generation. If a paid submission times out, inspect its remote task ID before retrying.

## 本地检查 / Local checks

```sh
npm run check
npm run dry-run
cp .dev.vars.example .dev.vars
# Edit .dev.vars locally with development credentials.
npm run dev
```

本地代理使用 HTTPS；浏览器可能需要信任 Wrangler 本地证书。`dry-run` 会实际构建 Docker 镜像，但不向 Cloudflare 发布。不要提交 `.dev.vars`。登录会话有效期 12 小时；轮换访问密码会使旧会话失效。退出入口为 `/_auth/logout`。

Local development uses HTTPS for secure cookies. Trust the local development certificate if needed. The dry run builds the image without publishing. Never commit `.dev.vars`. Sessions expire after 12 hours; rotating the access password invalidates old sessions. Sign out at `/_auth/logout`.

## 故障排查 / Troubleshooting

- **Docker unavailable**：启动 Docker/OrbStack，确认 `docker info` 可用。
- **503 setup incomplete**：配置满足长度要求的 `APP_ACCESS_PASSWORD`。
- **503 studio starting**：首次启动或镜像更新需要时间，稍后刷新；查看 `npx wrangler tail`。
- **模型 401/2013**：检查 Key、区域、额度及 H3 按量付费资格。
- **文件消失**：确认是否发生容器休眠/重启；本版本无法恢复未下载的临时作品。

Implementation uses the official [Containers SDK](https://github.com/cloudflare/containers) and [Wrangler](https://github.com/cloudflare/workers-sdk). Pin versions through `package-lock.json` and review platform changes before upgrades.

## GitHub 手动部署 / Manual GitHub deployment

仓库包含 `Deploy 青智焕新 to Cloudflare` 工作流，仅在手动触发时部署，不随推送自动发布。在 GitHub 的 `cloudflare` Environment 配置上述三个应用 Secrets，以及 `CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`，再在 Actions 选择此分支并运行。Token 需具备 Workers、Containers、Durable Objects 和镜像上传所需权限；首次使用建议先完成一次本地 OAuth 部署。

The included deployment workflow runs manually only. Configure the three application secrets plus `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in the GitHub `cloudflare` environment. Run it from Actions on the intended branch. A first local OAuth deployment is recommended to establish the project and account permissions.
