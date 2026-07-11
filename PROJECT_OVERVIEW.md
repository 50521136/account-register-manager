# Account Register Manager 项目说明

## 1. 项目定位

`account-register-manager` 是一个从 `yukkcat/chatgpt2api` 中抽离出来的独立账号池与注册管理服务。它主要用于：

- 管理本地 OpenAI / ChatGPT 账号池。
- 导入、刷新、编辑、删除和导出账号。
- 配置并运行自动注册任务。
- 通过多个邮箱 Provider 接收注册验证码。
- 将新注册账号按需同步到一个或多个 CLIProxyAPI 管理端。
- 提供一个轻量级本地 Web 管理页面。

项目默认不自动部署、不自动启动，需要手动运行本地服务或 Docker 服务。

## 2. 技术栈

- 后端：Python 3.11、FastAPI、Uvicorn。
- HTTP 客户端：`curl-cffi`、`requests`。
- 前端：单文件静态页面 `static/index.html`。
- 存储：本地 JSON 文件。
- 部署：Docker、Docker Compose。
- 自动同步：GitHub Actions 定时同步上游注册源码。

## 3. 目录结构

```text
.
├── account_register_manager/
│   ├── app.py                       # FastAPI 应用、路由、后台定时刷新任务
│   ├── account_service.py           # 账号池增删改查、刷新、导出
│   ├── auth.py                      # 管理端 Bearer Token 鉴权
│   ├── config.py                    # config.json 读取、更新、公开配置
│   ├── storage.py                   # JSON 文件存储封装
│   ├── register_service.py          # 注册任务配置、启动、停止、统计和日志
│   ├── openai_backend_api.py        # 调用 ChatGPT 后端接口刷新账号信息
│   ├── cliproxy_upload_service.py   # 同步账号到 CLIProxyAPI 管理端
│   ├── time_utils.py                # 北京时间工具
│   └── register/
│       ├── openai_register.py       # OpenAI 平台注册流程 Worker
│       └── mail_provider.py         # 邮箱 Provider 实现
├── static/index.html                # 本地管理台页面
├── scripts/sync_register_sources.py # 从上游 chatgpt2api 同步注册源码
├── data/                            # 运行时数据目录，包含敏感数据，已被 .gitignore 忽略
├── config.example.json              # 配置模板
├── config.json                      # 本地真实配置，已被 .gitignore 忽略
├── Dockerfile
├── docker-compose.yml
├── main.py                          # Uvicorn 入口
└── pyproject.toml
```

## 4. 快速启动

### 本地运行

复制配置模板：

```powershell
Copy-Item .\config.example.json .\config.json
```

修改 `config.json` 中的 `auth_key` 后启动：

```powershell
uvicorn main:app --host 127.0.0.1 --port 8010
```

浏览器打开：

```text
http://127.0.0.1:8010/
```

登录时输入 `config.json` 中配置的 `auth_key`。

### Docker 运行

```powershell
docker compose up -d --build
```

Compose 会挂载：

- `./config.json` 到容器内 `/app/config.json`
- `./data` 到容器内 `/app/data`

常用命令：

```powershell
docker compose logs -f
docker compose restart
docker compose down
```

也可以通过环境变量覆盖管理端登录密钥：

```powershell
$env:ACCOUNT_REGISTER_AUTH_KEY = "your-auth-key"
docker compose up -d --build
```

## 5. 配置说明

配置文件位置：`config.json`。

示例配置：

```json
{
  "auth_key": "change-me",
  "image_account_concurrency": 3,
  "auto_remove_invalid_accounts": false,
  "auto_remove_rate_limited_accounts": false
}
```

主要字段：

| 字段 | 说明 |
| --- | --- |
| `auth_key` | 管理台和 `/api/*` 接口的 Bearer Token |
| `outbound_proxy` | 后端访问外部服务时使用的出站代理 |
| `flaresolverr_enabled` | 是否在注册遭遇 Cloudflare 拦截时自动请求 FlareSolverr |
| `flaresolverr_url` | FlareSolverr 服务地址 |
| `flaresolverr_timeout_seconds` | FlareSolverr 单次请求超时秒数 |
| `flaresolverr_refresh_interval_seconds` | Clearance 缓存有效秒数 |
| `image_account_concurrency` | 图片账号并发相关配置，默认 3 |
| `auto_remove_invalid_accounts` | 刷新时发现无效 token 是否自动删除 |
| `auto_remove_rate_limited_accounts` | 更新为限流状态时是否自动删除 |
| `cpa_secret_key` | `/v0/management/auth-files*` 管理接口鉴权密钥，默认回退到 `auth_key` |
| `refresh_account_interval_minutes` | 周期性刷新账号信息的间隔，0 表示关闭 |
| `cliproxy_upload_targets` | 新注册账号同步到 CLIProxyAPI 的目标列表 |

管理台的“设置”页会调用 `/api/settings` 读取和更新这些字段。

## 6. 数据文件

运行时数据保存在 `data/` 目录下。该目录包含敏感数据，已经被 `.gitignore` 忽略。

| 文件 | 说明 |
| --- | --- |
| `data/accounts.json` | 账号池，包含 access token、refresh token、邮箱、额度、状态等信息 |
| `data/register.json` | 注册任务配置、邮箱 Provider 配置、统计信息 |
| `data/ddg_aliases.json` | DuckDuckGo 邮箱别名使用记录 |

注意：不要把 `config.json`、`data/accounts.json`、`data/register.json` 提交到仓库或公开分享。

## 7. 鉴权方式

管理接口统一使用 Bearer Token：

```http
Authorization: Bearer <auth_key>
```

相关逻辑在 `account_register_manager/auth.py` 中。若未配置 `auth_key`，管理接口会返回 500。

CLIProxyAPI 风格的管理接口使用 `cpa_secret_key`：

```http
Authorization: Bearer <cpa_secret_key>
```

## 8. 核心模块说明

### `app.py`

FastAPI 主应用，负责：

- 注册所有 HTTP API。
- 提供静态管理台首页 `/`。
- 启动生命周期内的周期性账号刷新线程。
- 提供账号、注册任务、设置、CLIProxy 同步和验证码查询接口。

### `account_service.py`

账号池核心服务，负责：

- 从 `data/accounts.json` 加载账号。
- 规范化账号字段。
- 添加、删除、更新账号。
- 调用 OpenAI 后端刷新账号信息。
- 导出完整账号凭证。
- 根据配置处理无效账号和限流账号。

账号状态常见值：

- `正常`
- `限流`
- `异常`
- `禁用`

### `openai_backend_api.py`

使用账号 `access_token` 调用 ChatGPT 后端接口，获取：

- 邮箱
- 用户 ID
- 套餐类型
- 图片额度
- 默认模型
- 限额恢复时间
- 账号状态

调用的主要路径包括：

- `/backend-api/me`
- `/backend-api/conversation/init`
- `/backend-api/accounts/check/v4-2023-04-27`

### `register_service.py`

注册任务调度层，负责：

- 读取和保存 `data/register.json`。
- 启动、停止、重置注册任务。
- 使用 `ThreadPoolExecutor` 并发提交注册 Worker。
- 维护注册统计、运行状态和日志。
- 按目标模式决定是否继续注册。
- 新注册账号成功后按配置同步到 CLIProxyAPI。

注册模式：

| 模式 | 说明 |
| --- | --- |
| `total` | 注册到指定总数后停止 |
| `quota` | 当前账号池图片额度达到目标后停止提交新任务 |
| `available` | 当前正常账号数达到目标后停止提交新任务 |

### `register/openai_register.py`

实际 OpenAI 平台注册流程 Worker，负责：

- 创建邮箱。
- 获取注册验证码。
- 完成 OpenAI/Auth0/Platform OAuth 流程。
- 生成账号密码和 token。
- 保存账号到账号池。
- 刷新账号信息。

如果使用 `xunmail` Provider，会额外把 `client_id` 和 `refresh_token` 保存到账号记录中，方便后续查询验证码。

### `register/mail_provider.py`

邮箱 Provider 适配层，支持的 Provider 包括：

- `cloudmail_gen`
- `cloudflare_temp_email`
- `ddg_mail`
- `tempmail_lol`
- `duckmail`
- `freemail`（兼容 `idinging/freemail` API）
- `gptmail`
- `moemail`
- `inbucket`
- `xunmail`
- `yyds_mail`

Provider 会从 `mail.providers` 中筛选 `enable=true` 的配置，并轮询选择可用 Provider。

### `cliproxy_upload_service.py`

用于把账号导出格式的数据上传到 CLIProxyAPI 管理端：

```http
POST /v0/management/auth-files?name=<account>.json
Authorization: Bearer <management-key>
Content-Type: application/json
```

每个 target 包含：

| 字段 | 说明 |
| --- | --- |
| `id` | 目标 ID，未传时自动生成 |
| `name` | 目标显示名称 |
| `base_url` | CLIProxyAPI 管理端地址 |
| `secret_key` | 管理端鉴权密钥 |
| `enabled` | 是否启用 |
| `timeout` | 上传超时时间 |

## 9. API 一览

所有 `/api/*` 接口都需要：

```http
Authorization: Bearer <auth_key>
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 返回静态管理台页面 |
| `GET` | `/api/settings` | 获取公开设置 |
| `POST` | `/api/settings` | 更新设置 |
| `POST` | `/api/settings/test-proxy` | 测试出站代理 |
| `POST` | `/api/settings/test-flaresolverr` | 测试 FlareSolverr clearance 获取 |
| `GET` | `/api/accounts` | 获取账号列表 |
| `POST` | `/api/accounts` | 导入账号或 token |
| `DELETE` | `/api/accounts` | 删除账号 |
| `POST` | `/api/accounts/refresh` | 同步刷新账号信息 |
| `POST` | `/api/accounts/refresh/start` | 启动后台刷新任务 |
| `GET` | `/api/accounts/refresh/job` | 查询后台刷新任务状态 |
| `POST` | `/api/accounts/update` | 更新账号状态、类型、额度 |
| `POST` | `/api/accounts/export` | 导出账号 JSON 或 ZIP |
| `POST` | `/api/accounts/check-code` | 查询 Outlook/Hotmail/Live/MSN 账号验证码 |
| `GET` | `/api/register` | 获取注册配置和状态 |
| `POST` | `/api/register` | 更新注册配置 |
| `POST` | `/api/register/start` | 启动注册任务 |
| `POST` | `/api/register/stop` | 请求停止注册任务 |
| `POST` | `/api/register/reset` | 重置注册统计和日志 |
| `GET` | `/api/register/events` | 注册状态 SSE 推送 |
| `POST` | `/api/cliproxy/upload/sync` | 手动同步账号到启用的 CLIProxyAPI 目标 |
| `GET` | `/v0/management/auth-files` | 以 CLIProxyAPI 风格列出账号文件 |
| `GET` | `/v0/management/auth-files/download` | 以 CLIProxyAPI 风格下载单个账号文件 |

## 10. 前端管理台功能

`static/index.html` 是一个纯静态单页管理台，主要功能包括：

- 登录并把 `auth_key` 保存到浏览器本地存储。
- 查看账号池列表、搜索、筛选状态。
- 导入 token 或完整账号 JSON。
- 批量刷新、删除、导出账号。
- 编辑账号状态和额度。
- 查询 xunmail 相关 Outlook 系列邮箱验证码。
- 配置注册任务、注册模式、线程数、代理和邮箱 Provider。
- 查看注册任务统计、日志和实时状态。
- 配置 CLIProxyAPI 上传目标并手动同步。
- 测试出站代理。
- 查看 CPA 管理接口示例。

## 11. 主要业务流程

### 导入与刷新账号

1. 前端提交 token 或账号 JSON 到 `/api/accounts`。
2. `AccountService` 规范化字段并写入 `data/accounts.json`。
3. 如果 `refresh=true`，服务会调用 ChatGPT 后端接口刷新邮箱、套餐、额度和状态。
4. 刷新结果写回账号池。

### 后台刷新账号

1. 前端调用 `/api/accounts/refresh/start`。
2. 后端创建后台线程，并用最多 10 个 worker 并发刷新。
3. 前端轮询 `/api/accounts/refresh/job` 展示进度。

如果 `refresh_account_interval_minutes > 0`，应用启动后还会周期性刷新全量账号。

### 自动注册账号

1. 前端保存注册配置到 `/api/register`。
2. 前端调用 `/api/register/start`。
3. `RegisterService` 按线程数提交 `openai_register.worker`。
4. Worker 创建邮箱、等待验证码并完成注册流程。
5. 成功后写入账号池并刷新账号信息。
6. 如果配置了 CLIProxyAPI 上传目标，则自动上传新账号。
7. 前端通过 `/api/register/events` 获取实时状态。

### 导出账号

1. 前端调用 `/api/accounts/export`。
2. `AccountService.build_export_items()` 筛选可导出的完整账号。
3. 支持返回单个 JSON、多个 JSON 数组或 ZIP 包。

可导出账号至少需要满足以下条件之一：

- 有 `access_token`、`refresh_token`、`id_token`。
- 有 `session_token`。
- 有邮箱和密码。

## 12. 维护与上游同步

注册相关源码来自上游 `yukkcat/chatgpt2api`，维护脚本为：

```powershell
python .\scripts\sync_register_sources.py
```

脚本会复制上游：

- `services/register/openai_register.py`
- `services/register/mail_provider.py`

然后自动替换为当前独立项目需要的 import 路径、数据目录路径和时间工具。

GitHub Actions 工作流：

```text
.github/workflows/sync-register-sources.yml
```

该工作流支持手动触发，也会按计划定时从上游同步并自动提交注册源码变更。

## 13. 安全注意事项

- `config.json`、`data/accounts.json`、`data/register.json` 都可能包含敏感信息。
- 不要把真实 token、邮箱凭证、代理凭证、CLIProxyAPI 密钥提交到仓库。
- 生产或公网部署时必须修改默认 `auth_key`。
- 如果暴露 `/v0/management/auth-files*` 接口，需要单独设置强度足够的 `cpa_secret_key`。
- Docker 部署时确认 `config.json` 和 `data/` 的宿主机权限。
- 出站代理可能影响所有账号刷新、注册和验证码相关请求。

## 14. 二开建议

- 新增管理接口时统一放在 `app.py`，并复用 `require_admin()`。
- 新增账号字段时优先在 `AccountService._normalize_account()` 中处理默认值和兼容字段。
- 新增邮箱 Provider 时继承 `BaseMailProvider`，并在 `_create_provider()` 中注册类型。
- 修改注册任务调度策略时优先调整 `RegisterService._target_reached()` 和 `_run()`。
- 修改账号导出格式时优先调整 `AccountService.build_export_items()`。
- 新增前端功能时保持 `static/index.html` 的单页模式，并沿用已有 `api()` 请求封装。

## 15. 当前项目状态小结

这个项目的核心是“本地 JSON 账号池 + FastAPI 管理接口 + 静态管理台 + 自动注册 Worker”。账号数据和注册配置都在本地文件中，后端通过 Bearer Token 保护接口，通过后台线程执行刷新和注册任务。整体结构较轻，适合本地部署、私有服务器部署或作为 CLIProxyAPI 账号文件来源。
