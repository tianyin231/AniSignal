# 部署与运维手册

[返回首页](../README.md) · [功能指南](features.md) · [Nginx 反代模板](examples/nginx.conf)

生产环境推荐使用 Docker Compose（4 容器形态）；本地开发可使用 SQLite 与进程内队列免容器启动。

---

## 1. 环境要求与支持矩阵

- **硬件基线**：建议内存 **≥ 2 GB**（运行态 MySQL + Redis + App + Collector 合计约 0.85 GiB，前端构建阶段与数据库缓冲需要额外余量），磁盘预留 ≥ 10 GB。
- **软件要求**：Git、Docker 24+（需包含 `docker buildx`）与 Docker Compose v2。
- **架构与系统**：
  - 官方镜像链支持 `linux/amd64` 与 `linux/arm64`（前端构建阶段通过 `--platform=$BUILDPLATFORM` 在宿主机原生架构编译，避免跨架构卡死；不支持 32 位 `armv7`）。
  - Windows 用户请在 **WSL2** 文件系统内克隆仓库并运行（仓库已配置 [.gitattributes](../.gitattributes) 强制脚本使用 `LF` 换行符，避免挂载 NTFS 目录导致 MySQL 性能或权限异常）。

---

## 2. Docker Compose 快速部署

### 2.1 准备 `.env`

```bash
git clone https://github.com/tianyin231/AniSignal.git
cd AniSignal
cp .env.example .env
```

编辑 `.env`（[compose.yaml](../compose.yaml) 已通过 `env_file` 将 `.env` 注入 `app` 与 `collector`，所有 `ANISIGNAL_*` 配置项均可直接在 `.env` 中声明）：

| 变量 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `MYSQL_ROOT_PASSWORD` | 是 | — | MySQL 密码（建议 `openssl rand -hex 24`；后端自动做 URL 编码，兼容任意特殊字符）。 |
| `ANISIGNAL_ADMIN_TOKEN` | 是 | — | `/admin` 管理端访问令牌（建议独立随机长字符串，禁止保留 `change-me`）。 |
| `ANISIGNAL_PUBLIC_BASE_URL` | 推荐 | `http://127.0.0.1:8090` | 对外完整访问基址（如 `https://signal.example.com`），自动同步至前端 Canonical/OG、Sitemap、robots.txt、RSS 与邮件链接。 |
| `WEB_PORT` | 否 | `127.0.0.1:8090` | 宿主机监听绑定。默认仅监听本机回环供反代转发；局域网/NAS 直连可改为 `0.0.0.0:8090`。 |
| `DATA_DIR` | 否 | `./data` | MySQL、Redis 与可选本地文件的持久化目录。 |
| `ANISIGNAL_EGRESS_ALLOW_CIDRS` | 否 | 空 | 出站 SSRF 额外放行网段。宿主机开启 Clash / mihomo TUN `fake-ip` 时设为 `198.18.0.0/15`（云元数据 `169.254.169.254` 始终拦截）。 |
| `ANISIGNAL_MAIL_HOURLY_LIMIT` | 否 | `100` | 配置 SMTP 后的全站每小时验证码发信上限，防止接口被刷信。 |
| `ANISIGNAL_YOUTUBE_API_KEY` | 否 | 空 | 可选 YouTube Data API v3 密钥；不填默认走免密钥频道 RSS。 |

### 2.2 启动与验活

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 app collector
```

- `app` 启动时自动通过 MySQL `GET_LOCK` 互斥执行 `alembic upgrade head`（新库通过 `0057_baseline` 一步建齐全部数据表，不写入任何作者私站演示数据），随后拉起 FastAPI（`127.0.0.1:8000`）与 Nuxt SSR（`:3000`）。
- 健康检查探测 `:3000/api/healthz`，同时验证前端 Nitro 代理与后端数据库连通性；公网内部路径 `/api/internal/**` 已在 Nitro 层直接拦截返回 `404`。
- 如需水平扩展采集并发，可执行 `docker compose up -d --scale collector=2`（调度器自动通过 Redis 原子续租选举单主，单信源采集与每日早报均带分布式互斥锁）。

---

## 3. 浏览器内零 SSH 初始化（3 步闭环）

访问 `http://127.0.0.1:8090/admin`（或你的域名 `/admin`），输入 `ANISIGNAL_ADMIN_TOKEN`：

1. **分区与信源（`/admin/targets`）**
   - **自定义分区**：在顶部「分区管理」面板可直接新建自定义分区（填写标识、名称、关键词别名），也可一键隐藏不需要的内置分区。顶栏导航仅展示已启用且存在活跃信源的分区。
   - **一键导入 / 批量移除**：点击「导入预设信源」可按勾选分区导入鸣潮、原神、星铁、AI 快讯等预设源（X 账号默认停用）；列表支持全选批量启用、停用或一键删除。
   - **命令行导入预设（可选）**：也可执行 `docker compose exec app uv run --project /app/server python -m app.seeds --all`（或 `--communities wuwa,ai`）。
   - **B 站登录态**：直接在同页点击「扫码登录」用手机 B 站 App 扫码，或粘贴 `SESSDATA` Cookie。凭据存入数据库并通过内存环境变量 `BILI_COOKIE` 传递给采集器，无需进服务器导文件。
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
2. 反代需透传 `Host`、`X-Real-IP`、`X-Forwarded-For` 与 **`X-Forwarded-Proto $scheme`**，并将 `.env` 中的 `ANISIGNAL_PUBLIC_BASE_URL` 设为 `https://你的域名`，以确保登录会话 Cookie 自动附带 `Secure` 标志。

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
docker compose up -d --build
```

### 首次启动失败自救
- 查看具体报错：`docker compose logs app mysql`
- 若*全新首次安装*因填错配置中断、尚未产生任何业务数据，可执行 `docker compose down && rm -rf ./data/mysql ./data/redis && docker compose up -d` 从干净状态重来（**已有数据的线上实例切勿删除 `./data`**）。

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

如需在本地模式调试 B 站采集，可从 [tamnd/bili Releases](https://github.com/tamnd/bili/releases) 下载对应平台（macOS / Linux / Windows，amd64 / arm64）的可执行文件放入 `PATH` 或设置 `ANISIGNAL_BILI_BIN`。

### 运行测试
```bash
# 后端单元测试
uv run --directory server pytest -q

# 前端测试
npm --prefix web test
```
