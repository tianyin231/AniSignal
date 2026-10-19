# 部署与本地开发

[返回首页](../README.md) · [功能指南](features.md)

完整部署使用 Docker Compose；本地开发可以使用 SQLite 与进程内队列。两种模式不要混用默认凭据，也不要把本地演示数据或页面可打开当作生产采集验收。

## Docker Compose

### 首次启动

需要 Git、Docker 和 Compose，以及拉取镜像、安装依赖所需的网络访问。构建内容以根目录 [Dockerfile](../Dockerfile) 为准，包含 Python 3.12、Node.js 22、Web 构建产物和 `bili` CLI。

```bash
git clone https://github.com/tianyin231/AniSignal.git
cd AniSignal
cp .env.example .env
```

编辑 `.env`，然后再启动。下面的字段由当前 [compose.yaml](../compose.yaml) 显式读取：

| 变量 | 用途 | 首次部署建议 |
| --- | --- | --- |
| `ANISIGNAL_ADMIN_TOKEN` | 管理员访问 Token | 设置独立的随机长字符串，不能保留 `change-me`。 |
| `MYSQL_ROOT_PASSWORD` | 数据库密码，同时用于应用连接串 | 设置另一串随机长密码。当前模板直接拼接连接 URL，使用足够长的随机十六进制值可避免 URL 特殊字符问题。 |
| `WEB_PORT` | 宿主机端口映射，未设置时为 `8090` | 本机试用填 `127.0.0.1:8090`；裸端口映射不是仅本机监听。 |
| `DATA_DIR` | MySQL 与 Redis 数据目录，默认 `./data` | 使用持久化目录并规划备份，不提交到仓库。 |
| `ANISIGNAL_PUBLIC_BASE_URL` | 对外站点地址，用于邮件、订阅等链接 | 对外部署时填写用户实际访问的完整地址。 |
| `ANISIGNAL_YOUTUBE_API_KEY` | YouTube Data API v3 凭据 | 可选；未配置时使用频道 RSS，API 失败时也会回退 RSS。 |

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app collector
```

本机打开 `http://127.0.0.1:8090`；`/api/healthz` 用于 API 健康检查，`/admin` 为管理页面。健康检查通过只说明对应服务可响应，采集、模型和消息通道需要分别验证。

### 运行结构

| 服务 | 职责 |
| --- | --- |
| `mysql` | 存储内容、版本、事件、用户与配置等持久化数据。 |
| `redis` | 任务队列与调度协调。 |
| `app` | 同一容器内运行 FastAPI 和 Nuxt SSR；Nitro 同源代理 `/api/**`。 |
| `collector` | 调度器与 arq worker，执行采集及相关后台任务。 |

应用启动时通过 [start-app.sh](../deploy/start-app.sh) 执行 `alembic upgrade head`，随后启动 API 和 Web。Compose 将 Web 端口映射到宿主机，没有直接发布 MySQL、Redis 或 API 的内部端口。

### 首次配置与检查

进入管理端，用 `.env` 中的管理员 Token 登录。配置采集目标并手动触发一次，核对来源状态、内容列表及原始链接；不要假设新数据库已经具备试运行站的全部数据与配置。

按需配置 SMTP、AI 模型接口及机器人通道，并逐项检查邮件投递、AI 用量和消息处理记录。公开访问还需要正确的站点地址、域名、HTTPS 与访问控制；不要使用开发 Token 或测试账号对外部署。

### 环境变量的两层含义

`server/app/config.py` 使用 `ANISIGNAL_` 前缀，例如 `ANISIGNAL_PUBLIC_BASE_URL`、`ANISIGNAL_SMTP_HOST` 和 `ANISIGNAL_AI_MODEL`。**但 Compose 的 `.env` 不是自动注入容器的配置文件**：只有 `compose.yaml` 引用或 `environment` / `env_file` 传入的变量才会进入服务环境。

当前 Compose 显式向 `app` 与 `collector` 传入数据库、Redis、管理员 Token、公开站点地址、YouTube API Key 和时区。公开地址与 YouTube Key 可直接在根目录 `.env` 中设置。SMTP、AI 等已有管理端配置入口；通过环境变量配置其他字段时，需要在本地 Compose override 中显式传入，实际优先级以代码为准。

例如，通过环境变量指定模型名时，可创建不含秘密值的本地 `compose.override.yaml`：

```yaml
services:
  app:
    environment:
      ANISIGNAL_AI_MODEL: ${ANISIGNAL_AI_MODEL}
  collector:
    environment:
      ANISIGNAL_AI_MODEL: ${ANISIGNAL_AI_MODEL}
```

在 `.env` 中设置对应值后，用 `docker compose up -d` 重建相关容器。反向代理改变了对外域名时，也要更新公开站点地址并核对邮件链接。凭据放在受保护的本地配置中，不写入文档或提交记录。

### 更新与备份

在确认工作区无未提交改动、数据库与配置已备份后，拉取已审核的版本，再运行 `docker compose up -d --build`。数据库迁移会随应用启动执行；更新前应先阅读变更，不能将代码回退视作数据库自动回退。

数据默认落在 `./data`。备份数据库、配置与必要的运行数据，并验证恢复流程；不要以删除数据目录作为常规排障方式。机器人是可选组件，根目录另有 [compose.astrbot.yaml](../compose.astrbot.yaml) 和 [插件目录](../integrations/astrbot_plugin_anisignal/)，启用前须单独完成其配置与检查。

## 本地开发：免 MySQL / Redis / Docker

需要 **Python 3.12+、uv、Node.js 22 和 npm**；Node.js 22 与仓库构建镜像保持一致。

在仓库根目录打开第一个终端：

```bash
cd server
uv run python -m app.local
```

另开一个终端，同样从仓库根目录执行：

```bash
cd web
npm ci
npm run dev
```

访问 `http://localhost:3000`。API 监听 `127.0.0.1:8000`，本地管理员 Token 默认为 `local-dev`，仅用于开发；可以通过 `ANISIGNAL_ADMIN_TOKEN` 覆盖。

[本地入口](../server/app/local.py) 会创建 `server/local.db`，使用 SQLite 和进程内队列，并在空目标表中播种示例采集目标。示例官网目标会访问真实公开来源；B 站示例目标默认停用，运行对应采集需要可用的 `bili` CLI。免数据库容器不等于离线运行或所有采集器无额外依赖。

本地模式不验证完整部署中的 MySQL 行为、Redis 队列、多副本调度或容器网络。已有本地数据库的结构变更也应单独处理，不能假定 `create_all` 会迁移旧表。

## 开发检查

后端测试：

```bash
cd server
uv run pytest
```

前端构建：

```bash
cd web
npm ci
npm run build
```

测试数量与结果以实际执行为准，不在介绍页写死。构建成功不等于浏览器交互通过；涉及页面或 API 的修改，应补充相应运行检查。

[后端说明](../server/README.md) · [前端说明](../web/README.md) · [配置定义](../server/app/config.py)
