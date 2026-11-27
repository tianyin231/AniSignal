# AniSignal 通用工程避坑与扩展备忘

> 本文档面向接手维护或参与贡献的开发者与 AI 编码工具，汇总在 AniSignal 代码库中已验证的**通用技术边界与常见陷阱**（不含任何私有服务器或个人部署信息）。修改对应模块前按需查阅即可。

---

## 一、后端、SQLAlchemy 与 FastAPI

- **SQLAlchemy `Result` 只能迭代一次**：需要对查询结果多趟遍历（如先按分区分组再过滤）时，务必先 `list()` / `.all()` 物化。
- **防止隐式笛卡尔积**：在 `WHERE` 子句中引用关联表（如 `ContentReview`）时，必须在查询中显式 `.join()`，否则结果集与 `total` 会成倍膨胀。
- **MySQL 排序缓冲区防溢出（1038 `Out of sort memory`）**：凡涉及 `RawContent` 关联 `ContentVersion` / `ContentReview` / `Source` 且带非索引表达式排序（如 `ORDER BY COALESCE(published_at, collected_at) DESC, id DESC LIMIT N`）的查询，严禁直接把宽表 ORM 实体或大 `JSON`/`TEXT` 列放进同一条带 `ORDER BY` 的 `SELECT`；一律采用**两阶段查 ID**——首查仅 `SELECT RawContent.id` 排序截断，次查按 `RawContent.id.in_(ids)`（无 `ORDER BY`）配合 `defer(...)` 跳过未用大字段并在 Python 按首查顺序重排。
- **FastAPI `Query` 可空正则**：筛选参数默认值为 `""` 且带 `pattern` 时，正则须写成 `^(|a|b)$`（写成 `^(a|b)$` 会导致空筛选返回 422）。
- **单测直接调用 FastAPI 端点函数**：绕过路由直接调用 handler 时，`Query(...)` 默认对象不会被解析，须显式传入全部参数。
- **多入口共享配置字段保护**：`/api/admin/settings/ai` 与 `/api/admin/ai/config` 共享同一份 `site_settings("ai")`；新增配置字段时，所有写入路径必须保留未传字段，避免误清空密钥池或定价配置。
- **更新端点字段同步**：给 `CollectTarget` 或 `CustomFeed` 新增配置项后，确认对应的 `PUT`/`PATCH` 端点与 Pydantic Schema 完整持久化，并补充单测。

---

## 二、数据库与 Alembic 迁移

- **先查最新 head 再建迁移**：新建迁移前检查 `server/alembic/versions/` 已有修订号，避免多分支并行产生双 head。
- **SQLite 测试与 MySQL 8.4 双栈兼容**：本地与单测默认跑 SQLite，CI 与生产跑空 MySQL 8.4 `alembic upgrade head && alembic check`。修改列类型或长度时，SQLite 须用 `with op.batch_alter_table(...)`、MySQL 用 `op.alter_column(...)`，防止 `compare_metadata` 报漂移。
- **MySQL 保留字与字符集**：手写 SQL/DDL 时注意用反引号包裹 MySQL 保留字（如 `delayed`）；客户端向 MySQL 写入中文须确保连接字符集为 `utf8mb4`。
- **存量行回填**：新增非空列或业务状态/策略枚举列时，必须给存量行回填合理默认值。

---

## 三、采集器、解析器与 AI 管线

- **B 站 `bili` CLI**：
  - 拉用户动态须使用独立的 `dynamics <mid>`，不要使用 `user <mid> --dynamics`（返回空数组）。
  - 视频详情风控熔断状态须存放在模块级 `_CIRCUIT_STATE`，不要挂在每次任务新建的采集器实例属性上。
- **YouTube 双链路回退**：配置 `ANISIGNAL_YOUTUBE_API_KEY` 后走 Data API v3，未配置或限流/失败时自动回退原生 RSS；社区频道须配置标题必含词防止跨主题串扰。
- **通用网页监测（`web_page`）**：
  - `trafilatura` 默认 250 字下限会丢弃短更新日志与价格表，已调整为 20 字并级联 `readability-lxml` + `html2text`。
  - SSRF 拦截、非 HTML 响应、选择器语法错误属于确定性错误，必须设置 `err.stop_fallback = True` 阻止代理出口无意义重试。
  - 列表链接提取需调用 `urldefrag` 去除 `#fragment`，且默认过滤常见 `?label=` / `?tag=` 标签筛选链接（除非用户 `link_pattern` 显式包含 `?`）。
- **AI 审核与实体抽取**：
  - 官方账号豁免相关性下架（新角色/新版本常超出模型知识边界）；相关性判定必须注入词表而非依赖模型记忆。
  - 模型调用失败保持 fail-open（不因接口波动误下架正常内容）；小模型长指令容易丢失尾部 JSON 字段，解析层须保留容错与确定性兜底。

---

## 四、前端（Nuxt 4 / Tailwind 4）与反代边界

- **Nitro 反代 Host 改写**：后端拼接对外 RSS、分享或回调链接时，禁止直接使用 `request.base_url`（反代环境下为内网地址），统一读取 `ANISIGNAL_PUBLIC_BASE_URL`。
- **随机壁纸链路**：壁纸原图由浏览器**直连图床 CDN**，禁止经由应用服务端中转压缩；`/wallpaper/download` 仅放行短期签发过的地址防止开放代理；`WallpaperToggle` 按钮需以 `status` 作为 `key` 重挂载避免 Vue patchFlag 子节点不更新。
- **Nuxt 自动导入重名检查**：`web/app/utils/` 下的具名导出会被 Nuxt 全局自动导入，跨文件复用类型或常量时避免在多个 utils 文件导出同名标识符（例如 `lexicon.ts` 导出 `PlatformKey`，`data.ts` 对外暴露 `Platform`）。
- **桌面表格内溢出菜单**：表格容器使用 `overflow-x-auto lg:overflow-visible`，并在末 1~2 行将溢出菜单向上翻转（`bottom-full`），避免 `⋯` 弹出层被滚动容器裁切。

---

## 五、AstrBot 插件开发协议（`integrations/astrbot_plugin_anisignal`）

- **版本号双处对齐**：代码心跳与上报版本统一取自 `main.py` 顶部 `VERSION` 常量，发版时须同步更新 `metadata.yaml`。
- **投递幂等与防重发**：`bot_deliveries` 状态机为 `pending → sending → accepted/failed/unknown`；发送中断绝不自动盲目重发，回执接口校验 `receipt`（409 视为凭据失效）。
- **QQ 官方单条图文**：图文混排须通过 `MessageChain().message(text).url_image(cover)` 单次 `context.send_message` 发送（不要用 `event.chain_result` 或分两次 `yield`，否则会被拆成两条消息或图片前置）。
- **发送前清理钩子与流式输出互斥**：若 AstrBot 启用了 `streaming_response=true`，会绕过 `on_decorating_result` 钩子导致第三方插件标签无法清理；在不支持真流式的平台（如 QQ 官方）建议关闭流式输出以保住清理链路。
- **平台泛化**：服务端与插件按 UMO 泛化，兼容 QQ 官方与 NapCat/OneBot（`aiocqhttp`）；指令类回复（`/早报`、`/资讯`、`/商品`、`/帮助`）使用结构化固定开场，不依赖 LLM 临场生成。
