# 部署与运维手册

[返回首页](../README.md) · [功能指南](features.md) · [Nginx 反代模板](examples/nginx.conf)

生产环境推荐使用 Docker Compose（5 容器单职责形态：`mysql` / `redis` / `api` / `web` / `collector`，共用官方多架构预构建镜像）；本地开发可使用 SQLite 与进程内队列免容器启动。

---

## 1. 环境要求与支持矩阵

- **硬件基线**：建议内存 **≥ 2 GB**（默认直接拉取 GHCR 预构建镜像，运行态合计约 0.85 GiB；若在 2 GB 小内存机器上执行本地 `--build`，建议额外开启 2 GB Swap），磁盘预留 ≥ 10 GB。
- **软件要求**：Git、Docker 24+ 与 Docker Compose v2。
- **架构与系统**：
  - 官方预构建镜像 `ghcr.io/tianyin231/anisignal:latest` 原生提供 `linux/amd64` 与 `linux/arm64` 双架构（不支持 32 位 `armv7`）。
  - **macOS（Apple Silicon / Intel）**：安装 Docker Desktop（或 OrbStack）并为其分配 ≥ 4 GB 内存即可直接执行 `docker compose up -d`。
  - **Windows**：请在 **WSL2** 文件系统内克隆仓库并运行（仓库已配置 [.gitattributes](../.gitattributes) 强制脚本使用 `LF` 换行符，避免挂载 NTFS 目录导致 MySQL 性能或权限异常）。
  - **RHEL / Rocky / Fedora（开启 SELinux）**：宿主机绑定挂载需要在 [compose.yaml](../compose.yaml) 的 `volumes` 路径末尾追加 `:z`（如 `${DATA_DIR:-./data}/mysql:/var/lib/mysql:z`，只读挂载改为 `:ro,z`），否则容器会报 `Permission denied`。

---

## 2. Docker Compose 快速部署

### 2.1 准备 `.env`

```bash
git clone https://github.com/tianyin231/AniSignal.git
cd AniSignal
cp .env.example .env
```

编辑 `.env`（[compose.yaml](../compose.yaml) 已通过 `env_file` 将 `.env` 注入 `api`、`web` 与 `collector`，所有 `ANISIGNAL_*` 配置项均可直接在 `.env` 中声明）：

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `MYSQL_ROOT_PASSWORD` | 是 | — | MySQL 密码（建议 `openssl rand -hex 24`；若含 `$` 或 `#`，请用单引号 `'...'` 包裹避免被 Compose 变量展开或当作注释截断）。 |
| `ANISIGNAL_ADMIN_TOKEN` | 是 | — | `/admin` 初始引导令牌（建议独立随机长字符串，禁止保留 `change-me`；含 `$` 或 `#` 时同样请加单引号；首次登录后可绑定个人站点账号免口令直进）。 |
| `ANISIGNAL_PUBLIC_BASE_URL` | 推荐 | `http://127.0.0.1:8090` | 对外完整访问基址（如 `https://signal.example.com`），自动同步至前端 Canonical/OG、Sitemap、robots.txt、RSS 与邮件链接。 |
| `WEB_PORT` | 否 | `127.0.0.1:8090` | 宿主机监听绑定。默认仅监听本机回环供反代转发；局域网/NAS 直连可改为 `0.0.0.0:8090`。 |
| `DATA_DIR` | 否 | `./data` | MySQL、Redis 与可选本地文件的持久化目录。 |
| `ANISIGNAL_IMAGE` | 否 | `ghcr.io/tianyin231/anisignal:latest` | 自定义镜像地址或固定版本标签（生产环境推荐锁定具体版本号如 `ghcr.io/tianyin231/anisignal:1.2.1`，避免代码仓库与浮动 `latest` 镜像版本错位）。 |
| `ANISIGNAL_EGRESS_ALLOW_CIDRS` | 否 | 空 | 出站 SSRF 额外放行网段。宿主机开启 Clash / mihomo TUN `fake-ip` 时设为 `198.18.0.0/15`（云元数据 `169.254.169.254` 始终拦截）。 |
| `ANISIGNAL_MAIL_HOURLY_LIMIT` | 否 | `100` | 配置 SMTP 后的全站每小时验证码发信上限，防止接口被刷信。 |
| `ANISIGNAL_YOUTUBE_API_KEY` | 否 | 空 | 可选 YouTube Data API v3 密钥；不填默认走免密钥频道 RSS。 |

### 2.2 启动与验活

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=80 api web collector
```

> 若修改了本地前后端源码，执行 `docker compose up -d --build` 重新构建本地镜像即可；如需限制单容器内存上限，可执行 `cp deploy/examples/compose.lowmem.yaml compose.override.yaml`（本地覆盖文件默认不入版本库）。

- `api`、`web` 与 `collector` 共用同一份镜像层（不额外占磁盘），但按单职责独立运行与独立输出日志：
  - `api` 启动时自动通过 MySQL `GET_LOCK` 互斥执行 `alembic upgrade head`（新库通过 `0057_baseline` 一步建齐全部数据表，不写入任何作者私站演示数据），随后以前台单进程运行 FastAPI（内部 `:8000`）；
  - `web` 在 `api` 健康就绪后启动 Nuxt 4 Nitro SSR（`:3000`），通过运行时环境变量 `NUXT_API_SSR_BASE=http://api:8000` 反代后端接口，并在入口层拦截 `/api/internal/**` 返回 `404`。
- 如需水平扩展采集并发，可执行 `docker compose up -d --scale collector=2`（调度器自动通过 Redis 原子续租选举单主，单信源采集与每日早报均带分布式互斥锁）。

---

## 3. 浏览器内零 SSH 初始化（3 步闭环）

访问 `http://127.0.0.1:8090/admin`（或你的域名 `/admin`），输入 `ANISIGNAL_ADMIN_TOKEN`（推荐先注册登录个人站点账号，在 `/admin/login` 勾选 **「同时将当前登录账号设为管理员」**，后续点击顶栏头像菜单「管理看板」即可免输 Token 直进后台，也可在 `/admin/users` 随时设/撤管理员）：

1. **分区与信源（`/admin/targets`）**
   - **自定义分区**：在顶部「分区管理」面板可直接新建自定义分区（填写 1~16 位英文标识、名称、关键词别名），也可点击「一键隐藏无信源分区」或手动启停内置分区。顶栏导航展示所有已启用的分区。
   - **一键导入 / 批量移除**：点击「导入预设信源」可按勾选分区导入鸣潮、原神、星铁、AI 快讯等预设源（X 账号默认停用）；列表支持标记「官方账号」、全选批量启用、停用或一键删除。
   - **命令行导入预设（可选）**：也可执行 `docker compose exec api python -m app.seeds`（支持 `--all` 或 `--communities wuwa,ai`）。
   - **B 站登录态（可选）**：不登录也能采集 B 站关键词搜索与 UP 主投稿；登录只是提高「UP 主动态」接口的成功率。需要时在同页点击「B 站扫码登录」用手机 B 站 App 扫码；「更多登录方式」里还有账号密码、短信验证码（支持区号）和粘贴 `SESSDATA` Cookie。凭据存入数据库并通过内存环境变量 `BILI_COOKIE` 传递给采集器，无需进服务器导文件。
     - 账号密码和短信登录都要先通过 B 站官方人机验证；密码会经你自己的服务器加密转发给 B 站，密码与验证码均不落库、不写日志。海外服务器用账密登录较易触发 B 站额外验证，优先用扫码。
2. **AI 模型与无 AI 降级（`/admin/ai`）**
   - 填写任意 OpenAI 兼容接口（Base URL、API Key 池、主模型/轻量模型），保存后点击测试连接即可。
   - **不配置 AI 时的行为边界**：游戏官网公告与视频的规则分类归并、RSS/B 站/YouTube 采集、关键词关注均正常工作；依赖模型的活动日历抽取、周边自动发现与外语翻译会自动跳过，`/ai` 快讯页会出现引导提示并自动支持切换 **「显示未审原文」** 直接阅读一手 RSS 原文。
3. **用户注册、邮件与站点信息（`/admin/users` & `/admin/settings`）**
   - **配置了 SMTP**：在 `/admin/settings` 填写并发测试信，即可启用注册/找回密码发码与每日 08:00 早报。
   - **未配置 SMTP**：登录/注册页会提示联系管理员；管理员可在 `/admin/users` 顶部的「待用注册码」面板直接复制验证码发给用户，或直接点击用户行的 **「激活」**、**「改密」** 完成账号开通与密码重置。
   - **站点合规信息**：在 `/admin/settings` 的站点文案中可配置 `contact_email`（公开法律页联系邮箱，默认留空不暴露任何私人邮箱）与 `source_url`（AGPL-3.0 源码地址）。

---

## 4. 反向代理与 HTTPS（Nginx）

仓库提供脱敏且可直接通过 `nginx -t` 校验的配置模板：[docs/examples/nginx.conf](examples/nginx.conf)。

关键配置要点：
1. 保持 `WEB_PORT=127.0.0.1:8090`，避免绕过 Nginx 与防火墙直接暴露 `:8090`；若前置 Cloudflare 等 CDN，建议在云安全组仅允许 CDN 回源 IP 段访问 `80/443`。
2. 反代需透传 `Host`、`X-Real-IP`、`X-Forwarded-For` 与 **`X-Forwarded-Proto $scheme`**，并在 `.env` 中设置 `ANISIGNAL_PUBLIC_BASE_URL=https://你的域名` 与 `TRUST_PROXY=1`，以确保真实访客 IP 透传且登录会话 Cookie 自动附带 `Secure` 标志。

---

## 5. 国内网络与代理排障

1. **构建阶段 npm 镜像源**：`Dockerfile` 默认使用官方源（`--replace-registry-host=always`）。国内机器构建缓慢时可传入构建参数：
   ```bash
   docker compose build --build-arg NPM_REGISTRY=https://registry.npmmirror.com
   docker compose up -d
   ```
2. **Clash / mihomo TUN `fake-ip` 模式**：开启 `fake-ip` 后 DNS 会返回 `198.18.0.0/15` 测试网段地址，触发默认公网 SSRF 拦截（报错提示解析到了非公网地址）。在 `.env` 中添加：
   ```dotenv
   ANISIGNAL_EGRESS_ALLOW_CIDRS=198.18.0.0/15
   ```
   然后执行 `docker compose up -d` 重建容器即可。
3. **采集出口代理池**：若部分境外信源（YouTube / X / 海外 RSS）需要走 HTTP/SOCKS 代理，可在后台 `/admin/proxies` 按平台配置代理池，无需重启容器。

---

## 6. 备份、升级与首次安装自救

### 备份与恢复
持久化数据位于 `${DATA_DIR:-./data}`。建议定期逻辑备份 MySQL 数据库与 `.env`：

```bash
# 导出压缩备份
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --routines anisignal' | gzip > anisignal-$(date +%F).sql.gz

# 恢复到空库
gunzip -c anisignal-2026-11-13.sql.gz | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot anisignal'
```

### 升级流程
确认已备份数据库后：

```bash
git pull
docker compose pull
docker compose up -d --remove-orphans
```

（若你基于本地修改构建镜像，改为执行 `docker compose up -d --build --remove-orphans`。）

- **旧版 B 站 Cookie 文件迁移**：早期以宿主机 `0600` 文件方式挂载 `cookies.txt` 的老实例升级到非 root 镜像后，可执行一次 `docker compose exec -T --user root api python -m app.bilibili_login --import-to-db --cookie-file /run/anisignal/bilibili/cookies.txt` 将凭据导入数据库，后续不再依赖宿主机文件权限。

### 首次启动失败自救
- 查看具体报错：`docker compose logs api web mysql`
- 若*全新首次安装*因填错配置中断、尚未产生任何业务数据，可执行 `docker compose down && sudo rm -rf ./data/mysql ./data/redis && docker compose up -d` 从干净状态重来（Linux 下数据目录归属容器内用户，删除需 `sudo`；**已有数据的线上实例切勿删除 `./data`**）。

---

## 7. 本地开发（免 Docker / MySQL / Redis）

需要 **Python 3.12+、uv、Node.js 22 和 npm**：

```bash
# 终端 1：启动本地后端（SQLite ./server/local.db + 进程内队列，默认管理令牌 local-dev）
cd server
uv run python -m app.local

# 终端 2：启动前端开发服（http://localhost:3000）
cd web
npm ci
npm run dev
```

如需在本地模式调试 B 站采集，可从 [tamnd/bilibili-cli Releases](https://github.com/tamnd/bilibili-cli/releases) 下载对应平台（macOS / Linux / Windows，amd64 / arm64）的可执行文件放入 `PATH` 或设置 `ANISIGNAL_BILI_BIN`。

### 运行测试
```bash
# 后端单元测试
uv run --directory server pytest -q

# 前端测试
npm --prefix web test
```
