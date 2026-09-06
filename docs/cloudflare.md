# 青智焕新：Cloudflare 部署 / Cloudflare deployment

使用一个 Worker 作为受密码保护的入口，一个 Cloudflare Container 运行 Streamlit、Python 和 FFmpeg。全站 HTTP、文件下载及 WebSocket 均需认证。默认一个 4 GiB `standard-1` 容器，60 分钟无活动后休眠。运行中任务依赖容器存活；关闭页面后仍可能因休眠或平台重启而中断。

A password-protected Worker forwards traffic to one Python/FFmpeg container. Authentication covers app assets, downloads and WebSockets. This is a single trusted-owner workspace with shared application configuration, not a multi-tenant service.

## 前提 / Prerequisites

- Cloudflare 付费账户并开通 Containers；它不是纯免费 Pages 站点。运行资源和模型生成分别计费，请在控制台查看当前价格及额度。
- Node.js 22+，运行中的 Docker（macOS 可用 OrbStack 或 Docker Desktop）。首次构建需要下载 Python 媒体依赖，可能较慢。
- DeepSeek API Key，以及 MiniMax **按量付费** API Key（H3 不使用 Token Plan/OAuth 订阅凭据）。

## 部署 / Deploy

先在 Cloudflare R2 创建专用私有 Bucket（例如 `qingzhi-huanxin`），保持公共访问关闭。创建仅限该 Bucket 的 **Object Read & Write** R2 API 凭据，保存生成的 Access Key ID 和 Secret Access Key；它们与 Wrangler 的 Cloudflare API Token 不同。客户端不会收到这些密钥，也不需要开放 Bucket CORS。容器直接使用 R2 官方 S3 API，通过分段上传保存大文件。

Create a dedicated private R2 bucket and bucket-scoped Object Read & Write credentials. Keep public access disabled. R2 S3 credentials are separate from the Cloudflare deployment API token. The server accesses R2 directly; browser CORS and public bucket URLs are unnecessary.

在仓库根目录开始 / Start from the repository root:

```sh
cd cloudflare
npm ci
npm run login
npx wrangler whoami
npm run secrets
npm run deploy
```

`npm run secrets` 依次安全询问七个配置项；不要把真实密钥写入代码或聊天：

| Secret | Purpose |
|---|---|
| `APP_ACCESS_PASSWORD` | 随机访问密码，至少 24 个字符 / Random access password, at least 24 characters |
| `DEEPSEEK_API_KEY` | DeepSeek 脚本创作 / Script generation |
| `MINIMAX_API_KEY` | 官方 H3 视频和 image-01 图片 / Official media generation |
| `R2_ENDPOINT_URL` | R2 S3 endpoint，例如 `https://<32位账户ID>.r2.cloudflarestorage.com` |
| `R2_BUCKET_NAME` | 本工作室专用私有 Bucket 名称 / Dedicated private bucket |
| `R2_ACCESS_KEY_ID` | 限定该 Bucket 的 R2 S3 Access Key ID |
| `R2_SECRET_ACCESS_KEY` | 对应的 R2 S3 Secret Access Key |

首次写 secret 时 Wrangler 可能提示创建 Worker，请按提示创建 `qingzhi-huanxin`。部署完成后访问命令输出的 HTTPS 地址，输入访问密码。设置 → 素材 API 中选择与 MiniMax Key 一致的区域：国际 `api.minimax.io` 或国内 `api.minimaxi.com`。访问密码未设置或短于 24 字符时服务返回 503，不会公开应用。

The first secret write may ask to create the Worker. Visit the HTTPS URL returned by deployment and sign in. Select the MiniMax region matching your account in Settings → Material API. Missing/short access passwords fail closed with HTTP 503.

## 文件和任务 / Files and tasks

- Cloudflare 默认启用 `QINGZHI_PERSISTENCE=r2`。每个任务的状态、进度、主题、脚本、失败原因以及已收到的 MiniMax H3 远端任务 ID 保存在 `qingzhi/v1/tasks/`。视频、图片、合并视频、音频和字幕成品保存在 `qingzhi/v1/media/`。不保存 API 密钥、完整设置、原始上传素材和日志。
- 先上传成品，再保存完成记录。出现“尚未保存到 R2”时，作品只在本地；立即下载，进入“作品与任务”点“重试保存”。此操作只重试存储，不再次调用模型。只有显示已保存的作品才能保证重启后恢复。
- 重启后从 R2 加载历史。在“作品与任务”点击“打开作品”会按需下载到本地，并校验 SHA-256；列表不会自动下载全部作品。可预览和下载视频、图片，复制已保存脚本。
- 正在执行或排队的任务在重启后标记为“失败或中断”，保留进度与远端 ID。**不会自动继续流水线、重新提交付费请求或自动补领远端结果**。请先核查 MiniMax 任务和账单，再决定重新生成。提交成功与记录远端 ID 之间仍有极短的故障窗口，不能承诺恰好一次计费。
- 永久删除会删除该任务的 R2 记录和记录引用的成品。删除中断时保留删除标记，下次启动继续清理。未完成上传可能留下无记录引用的对象；管理员可核对记录后清理，不要对全部媒体设置短期自动到期。建议为未完成的 multipart uploads 配置清理规则。
- R2 连接或配置错误会停止启动，不会静默切回空白内存历史。R2 暂不可用时保留已有本地下载；新任务记录保存失败会阻止提交。生成期间保存失败的任务需手动重试同步。
- 每个 Bucket 只供一个工作室使用，保持 `max_instances: 1` 和固定 `qingzhi-owner`。这不是多进程共享任务队列或多用户系统；不要让两套部署同时写同一 Bucket。任务索引在进程启动时分页加载到内存，适合个人工作室。
- 容器本地磁盘仍为临时缓存；设置需导出。启用持久化之前的旧本地历史不会自动迁移，请在升级前下载。云端单文件上传最多 90 MB，独立拼接总计最多 500 MB、20 片段、10 分钟。

Cloudflare enables R2 persistence by default. Completed media is uploaded before its task record is committed; unsaved local results can be saved again without regeneration. The library restores media on demand with checksum verification. Interrupted or queued jobs become failed after restart, with available provider task IDs retained; paid tasks are never automatically resubmitted. This persists history and results, not an automatically resumable execution queue. Settings, raw uploads, logs and old pre-R2 history are not migrated.

R2 startup failures fail closed. Use a dedicated bucket with one owner/container writer. Records load once at startup with pagination; this design suits a personal studio. Deletion uses a durable tombstone before removing referenced objects. Review orphaned media and clean up incomplete multipart uploads separately.

本地 Python 部署也可设置 `QINGZHI_PERSISTENCE=r2` 和上述四个 R2 环境变量；默认本地部署仍使用原有 Memory/Redis。R2 与 Redis 任务状态不能同时启用。

Local Python deployments can opt in with `QINGZHI_PERSISTENCE=r2` and the four R2 environment variables. Memory/Redis remains the local default; R2 and Redis state must not be enabled together.

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
- **文件消失**：进入“作品与任务”重新打开作品恢复缓存；若先前提示 R2 保存失败，需使用自己下载的副本。

Implementation uses the official [Containers SDK](https://github.com/cloudflare/containers) and [Wrangler](https://github.com/cloudflare/workers-sdk). Pin versions through `package-lock.json` and review platform changes before upgrades.

## GitHub 手动部署 / Manual GitHub deployment

仓库包含 `Deploy 青智焕新 to Cloudflare` 工作流，仅在手动触发时部署，不随推送自动发布。在 GitHub 的 `cloudflare` Environment 配置上述七个应用配置 Secrets，以及 `CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`，再在 Actions 选择此分支并运行。Token 需具备 Workers、Containers、Durable Objects 和镜像上传所需权限；首次使用建议先完成一次本地 OAuth 部署。

The included deployment workflow runs manually only. Configure the seven application secrets plus `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in the GitHub `cloudflare` environment. Run it from Actions on the intended branch. A first local OAuth deployment is recommended to establish the project and account permissions.

R2 integration follows Cloudflare’s official [Python boto3 example](https://developers.cloudflare.com/r2/examples/aws/boto3/) ([source](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/r2/examples/aws/boto3.mdx)). Credentials stay server-side and downloads pass through the authenticated Streamlit interface.
