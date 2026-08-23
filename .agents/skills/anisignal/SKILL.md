---
name: anisignal
description: "AniSignal（鸣潮跨平台事件雷达）项目专属指南。在本仓库做任何工作之前先读本 skill：后端采集器、worker 任务队列、管理员看板、MySQL 建模、Redis 消息、Nuxt 前端、docs 文档或 Docker 部署都适用。包含已确定的架构决策、已实测验证的数据源采集方案（B 站 bili-cli、米游社、库洛官网 CDN、YouTube RSS）、合规红线和仓库结构。凡是涉及'采集/抓取/爬虫/关键词/定时任务/事件/履约/早报/监控位'的实现或修改，必须先读本 skill。"
---

# AniSignal 项目指南

《鸣潮》跨平台事件雷达：把多平台原始内容归并为**可追溯的事件**，用户订阅后不再蹲评论区。首版聚焦库洛游戏《鸣潮》+ 爱弥斯联名键盘的履约追踪，每天 08:00（北京时间）早报 + 重要状态变化即时邮件。

完整产品与系统设计见 `docs/superpowers/specs/2026-08-12-wuthering-waves-event-radar-design.md`（约 530 行，涉及产品边界、领域模型、事件/证据规则时先读它）。本 skill 只保留动手时必须记住的结论。

## 仓库现状

| 部分 | 位置 | 状态 |
|---|---|---|
| 产品设计文档 | `docs/superpowers/specs/` | 已定稿，评审通过 |
| Web 前端 | `web/`（Nuxt 4 + Tailwind 4） | 原型可用；`/admin` 管理看板已接真实后端 |
| 后端/采集/调度 | `server/`（FastAPI + arq + compose） | 采集闭环稳定运行中；**事件引擎 v1 已上线（2026-08-23）**：规则分类（词典可管理）→ 公告合并/商品履约聚合 → 事件+证据+修订落库，51 个单测全过 |
| 部署 | 根目录 `Dockerfile` + `compose.yaml` | **4 容器形态（2026-08-23 定稿）**：mysql / redis / app（FastAPI+Nitro 同容器，对外仅 8090，`/api/**` 同源代理，管理接口在 `/api/admin/**`）/ collector（调度+worker 同容器，Redis 领导锁支持 `--scale collector=N`）。数据 bind mount 在 `./data/`（服务器 /www 盘）；单镜像含 bili+python+node |

`server/` 关键路径：`app/services/classify.py`（规则分类+词典种子）、`app/services/events.py`（聚合引擎：72h+标题公共子串聚类、履约阶梯、证据重叠合并——宁缺勿滥，不同主题不合并）、`app/services/views.py`（事件/商品 API 序列化）、`app/collectors/`（平台采集器）、`app/parsers/`（统一 schema 解析）、`app/services/ingest.py`（幂等入库）、`app/services/collect.py`（编排+健康）、`app/services/queue.py`（BaseQueue：ArqQueue 生产 / LocalQueue 进程内）、`app/scheduler/loop.py`、`app/worker/settings.py`、`app/api/admin.py`、`app/local.py`（**本地测试入口：`uv run python -m app.local`，SQLite+进程内队列，免 Docker/MySQL/Redis**）。新平台接入 = collector + parser + 在 `ingest.PARSERS` 与 `collect._COLLECTORS` 各注册一行。

平台验证笔记原文在 `.worktrees/platform-probe/docs/platform-integration-notes.md`，但该目录不被 git 跟踪；**唯一持久副本是本 skill 的 `references/data-sources.md`**，引用时以后者为准。

## 已确定的架构（2026-08-22 与所有者确认）

模块化单体思想 + Docker Compose 部署，不拆微服务：

```text
管理员看板（关键词、定时频率等配置）
        │ 写入配置
        ▼
      MySQL（主存储：配置、原始内容、事件、证据、用户）
        │ 调度器读取配置，投递任务
        ▼
      Redis（任务队列 / 消息交换 / 去重 / 限流）
        │
        ▼
采集 Worker（Docker 部署，按平台分适配器：B站/米游社/官网/YouTube）
        │ 原始 JSON 入库（不可覆盖）
        ▼
清洗 → 去重 → 实体关联 → AI 翻译/摘要/分类候选 → 事件引擎 → 通知
```

- **采集层 Docker 化**：B 站使用已验证的 `ghcr.io/tamnd/bili:latest` 镜像匿名只读访问（详见 references/data-sources.md）。
- **管理员看板**维护关键词、账号 UID、采集频率；生产者/调度器据此生成任务。
- **Redis** 负责任务分发、内容去重指纹、平台级限流令牌、来源健康状态。
- **MySQL** 存主数据：原始内容、指标快照、事件、证据、监控规则、通知记录。
- **worker 常驻运行**（不做一次性容器），定时采集由调度器按看板配置投递；初期一个 worker，后期可加副本横向扩展，状态全部进 MySQL/Redis。

## 技术栈（2026-08-22 与所有者确认）

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2（异步）+ Alembic 迁移 |
| 任务队列 | arq（async 原生、Redis 后端、支持常驻 worker 与 cron） |
| 存储 | MySQL 8（utf8mb4）+ Redis 7 |
| 采集 CLI | bili 二进制经多阶段构建（`FROM ghcr.io/tamnd/bili AS bili` + `COPY --from=bili`）拷入 worker 镜像，subprocess 调用，不挂 docker socket |
| 公开前端 | `web/` Nuxt 4 + Tailwind 4（已有原型） |
| 管理看板 | web 同仓 `/admin` 路由组：独立 layout + 登录守卫，一份构建部署，反代层可加 IP 限制 |

## 数据解析层（采集 → 存储之间）

```text
采集适配器 → 原始 JSON 先落库（不可覆盖）→ 解析器映射统一 schema
→ 指标写时间快照表 → 下游去重 / 实体关联 / AI
```

- 每平台一个解析模块，输出统一 schema：platform、external_id、内容类型、作者、标题、正文、链接、发布时间（UTC）、媒体列表、指标 dict。
- 原始 JSON 与标准化字段分列存；解析失败不丢原始数据，可随时重跑。

## 核心领域模型（设计文档 §7）

```text
Publisher -> Game -> Region -> OfficialAccount
Game -> Character -> Product -> SKU
Source -> RawContent -> ContentVersion -> Evidence -> Event
User -> WatchRule -> Notification -> DeliveryRecord
```

三条不可违反的数据原则：

1. **原始数据不可覆盖**——采集结果原样入库，修改产生新 ContentVersion。
2. **AI 结果可以重算**——翻译/摘要/分类只是候选，不阻塞入库，可全量重跑。
3. **事件结论可以修正**——事件保留支持/冲突证据、可信度理由和人工修正记录。

**账号系统（2026-08-23 上线）**：邮箱+密码（scrypt，Python 标准库），服务端会话（httpOnly cookie；`users/email_codes/user_sessions` 表；改密/重置吊销全部会话）；验证码 6 位/15 分钟/尝限 5 次，发码与登录均限频；登录/找回统一文案防枚举。邮件走通用 SMTP（`ANISIGNAL_SMTP_*` env），未配置 SMTP 时验证码落库、管理端 `/api/admin/email-codes` 可查（开发通道）。前端 `/login /register /forgot /account` + `useAuth` composable。

播放量、点赞等指标一律存**时间快照**（用于判断首次越过阈值的时刻），不要只存最新值。

## 合规红线（设计文档 §13 + 实测边界）

- 只用**匿名、公开、只读**接口；不绕过登录、验证码、风控、速率限制；不伪造/盗用 Cookie 或 token。
- 平台条款风险不因"服务器在海外"或"页面公开"消失；每个适配器可独立关闭，支持人工补录。
- 低频请求 + 缓存 + 重试；B 站单次搜索实测 1.7–2.0 秒，不要并发轰炸。
- 不长期镜像完整视频/图片；视频用 B 站官方外链播放器嵌入（`https://player.bilibili.com/player.html?bvid=...`）。
- 采集任务必须幂等：同一内容重复采集不产生重复记录（MySQL 按 `平台 + 外部ID` 唯一键，Redis 存内容指纹）。

## 采集实现速记（详细命令与字段见 references/data-sources.md）

动手写采集代码之前**必须先读 `references/data-sources.md`**，那里有每个平台的已验证命令、返回字段和已知陷阱。速记：

- B 站走 `ghcr.io/tamnd/bili:latest` Docker CLI；`/data` 卷是响应缓存。
- 动态用 `dynamics <mid>` 入口，不要用 `user <mid> --dynamics`（后者返回空）。
- 搜索结果的统计字段不全：**搜索拿 BV 号 → 再请求 `video` 详情作为最终数据**。
- 鸣潮官方账号 UID 是 `1955897084`，但必须做成配置项，禁止硬编码。
- 部分图文动态 `text` 为空是已知限制，不能假设正文都已展开。
- 首版四条稳定链路：B 站 bili-cli、米游社 searchPosts→getPostFull、鸣潮国服/国际服官网公告 CDN；YouTube RSS 作低成本补充。
- 小黑盒、X、小红书、抖音、森空岛首版不接入（需登录或未验证）。

## 其他必须遵守的约定

- **时间**：存储统一 UTC，展示与早报按北京时间（08:00 早报、07:50 截稿）。
- **MySQL**：`utf8mb4` 字符集；唯一键 `(platform, external_id)`；原始 JSON 与标准化字段分开存。
- **"没抓到"≠"没有消息"**：来源失效必须显式呈现为不可用/数据可能不完整（来源健康表：最近成功时间、连续失败次数）。
- **前端约定**见 `web/README.md`：论坛式三栏布局、macOS 材质、主色论坛蓝、无紫色系、身份标签色系（官方=蓝/送测=琥珀/普通用户=绿/媒体=天蓝/商家=青绿/未说明=灰）。
- 后端探针代码用 Python 3.11+（httpx），新增后端服务沿用 Python 生态保持一致。

## 何时读哪个文件

| 要做的事 | 先读 |
|---|---|
| 写采集适配器 / 调度 / worker | 本文件 + `references/data-sources.md` |
| 设计 MySQL 表结构 / 事件引擎 | 本文件 + 设计文档 §7–§9 |
| 通知、早报、监控规则 | 设计文档 §10 |
| 前端页面 | `web/README.md` |
| 产品边界争议 | 设计文档 §3（目标/非目标）与 §17（扩张闸门） |
