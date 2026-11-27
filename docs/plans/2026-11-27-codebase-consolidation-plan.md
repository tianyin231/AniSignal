# 代码架构收束与多形态前端减负规划

- **日期**：2026-11-27
- **状态**：P0 / P1 已落地（按决策 A2 + B + C 执行完成），P2 历史超大文件保持按需推进
- **关联规划**：[`docs/plans/2026-11-25-general-sources-and-reports.md`](2026-11-25-general-sources-and-reports.md)

---

## 1. 背景与当前代码基线

### 1.1 技术栈确认（单框架，非多框架）

前端始终只维护**一套统一技术栈**：`Nuxt 4.7` + `Vue 3` + `Tailwind CSS 4` + 统一 `--sd-*` 语义设计令牌（`web/app/assets/css/main.css`），不存在多套前端框架混用的问题。

当前体感上的“割裂与膨胀”，根源是**多期业务迭代在同一个仓库里叠加了多套并存的页面形态与采集模式，而早期试水形态没有及时退场**。

### 1.2 代码行数演化快照（截至 `7b1caae`）

| 里程碑提交 | `web/app` | `server/app` | `server/tests` | 三项合计 | 相对上一阶段净增 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| 通用网页监控前（`2fa7db5~1`） | 23,458 | 39,145 | 26,034 | 88,637 | — |
| 开源功能改动前（`0a5d263`） | 24,575 | 41,016 | 26,623 | 92,214 | +3,577 |
| 首轮整理前（`946ca6f`） | 26,836 | 43,933 | 27,918 | 98,687 | +6,473 |
| 收束前基线（`7b1caae`） | 26,659 | 44,021 | 27,918 | 98,598 | -89 |
| **P0 + P1 收束后（2026-11-27）** | **26,236** | **43,501** | **27,705** | **97,442** | **-1,156** |

> 本轮活跃生产代码（`web/app` + `server/app`）真实净减 **-943 行**（零死文件充数），含单测合计净减 **-1,156 行**。

### 1.3 首轮整理（`7b1caae`）复盘与教训

`7b1caae` 表面净减 188 行（`+441 / -629`），但拆分真实账目后可见：

- 删除 6 个历史零引用死文件：**-215 行**
- 后端 `public.py` 合并重复话题聚合逻辑：**-11 行**
- **活跃前端抽共用组件部分反而净增 +38 行**：
  - 新建 `SignalHero.vue`（+86）、`BarDistributionCard.vue`（+78）、`MarkdownBlocks.vue`（+55）与扩展 `SourceHealthMini.vue`（+28）= **+247 行**
  - 6 个调用方省下的重复模板 = **-209 行**

**核心结论**：只把样式外壳抽成新组件会引入 `defineProps` / `defineEmits` / 插槽包装等样板开销，几乎不能实质瘦身。**真正减重必须靠合并业务形态、砍掉已弃用或过度设计的分支能力。**

---

## 2. 执行前需拍板的 3 个产品决策

后续动手前，先确认以下边界，避免误删仍在使用的功能或继续背负死负担：

1. **决策 A（最大减重项）：开源监控是否只保留「GitHub Trending 刊物」？**
   - **现状**：一阶段（`051d244`）落地了完整的 GitHub + GitCode 仓库 Release / Tag / Issue / Discussion 变更监控（含 REST API、Atom 回退、ETag 缓存与用户定制监控）；后续方向收敛为只抓 `github.com/trending` 日刊/周刊/月刊 + 仓库 README + AI 中文解读，但一阶段代码全量留存。
   - **选项 A1（推荐，大幅减重）**：明确放弃仓库级变更追踪与 GitCode，仅保留 GitHub Trending 日/周/月刊与 README 抓取。
   - **选项 A2（保留功能）**：若希望普通用户仍可在「定制监控」里填任意 GitHub 仓库追发版，则保留 GitHub Releases，仅评估是否移除 GitCode 及低频的 Issues/Discussions。

2. **决策 B：`/contents` 页面是否统一成单一内容流骨架？**
   - **现状**：`web/app/pages/contents/index.vue`（514 行）内部同时维护「主题流 2 栏版（开源/小黑盒）」与「游戏内容库单栏旧版」两套完整过滤栏和列表，主题流还额外保留「图文卡片 / 紧凑列表」双视图切换。
   - **目标**：合并为一套统一内容流模板，按分区开关控制是否显示顶部 Hero、话题栏与侧栏，并评估下线极低频的「卡片 / 紧凑列表」双切换。

3. **决策 C：是否同步治理历史超大单文件？**
   - 管理端与日历等存量页面体积较大（如 `admin/targets.vue` 1,576 行、`calendar.vue` 974 行），建议列为 P2，只在对应模块有功能改动时顺手重构，不单独为了拆文件而大改稳定代码。

---

## 3. 分期收束与减重方案

### P0：后端开源采集按边界裁切 ✅ 已按选项 A2 落地（2026-11-27）

- 裁除 `GitcodeCollector`、`gitcode` 平台与 `issues` / `discussions` / `gh_issues` / `gh_discussions` / `gc_releases` / `gc_issues` 低频分支及标签筛选逻辑，保留 **GitHub Trending（日/周/月刊 + 匿名 README 抓取）** 与 **GitHub Releases（后台目标 + 用户定制监控 `gh_releases`）**；
- 将原 1,189 行的 `server/app/collectors/git_repo.py` 拆分为 `server/app/collectors/github.py`（469 行）与 `server/app/collectors/github_trending.py`（283 行），解析器收束为 `server/app/parsers/github.py`（97 行）；
- 响应 DTO（`CollectTargetOut`、`ContentItemOut`、`PublicContentItem`、`ContentDetailOut`、`PublicSource`）放宽为 `str` 兼容任何历史存档行，写入端（`CollectTargetCreate`、定制监控校验）仍严格拦截已移除类型；
- 同步精简前端 `lexicon.ts`、`custom-feeds.ts`、`Monitors.vue`、`admin/targets.vue`、`admin/customs.vue` 与文档。

---

### P1：前端内容流、卡片与详情页多轨合一 ✅ 已按决策 B 落地（2026-11-27）

重点收束 4 个核心文件（合计从 1,717 行降至 1,358 行，配合 P0 前端同步精简 `web/app` 净减 **-423 行**）：

1. **统一内容列表骨架**（`web/app/pages/contents/index.vue`：514 → 388 行）
   - 合并主题流（`domain="feed"`）与游戏内容库（`domain="game"`）为单一内容流模板，按分区按需挂出话题筛选与右侧信号栏；
   - 下线主题流中冗余的「图文卡片 / 紧凑列表」双视图切换。
2. **收束卡片呈现形式**（`GitTrendingDesk.vue` 413 → 250 行，`TopicFeedCard.vue` 233 → 283 行，合计净减 113 行）
   - 新增纯函数 `trendingEntryToRawItem`（含单测），将 Trending 榜单行卡片主体复用至 `TopicFeedCard`，`GitTrendingDesk` 专注刊期切换、筛选与侧栏归档，点击 AI 标签可直接联动主题流筛选；
   - 修正 `TopicChannelHero.vue` 桌面端榜首信息与右侧 `#01` 卡片重复展示的问题。
3. **拍平站内详情页分支**（`web/app/pages/contents/[id].vue`：557 → 437 行）
   - 收敛为三段清晰区块：统一头部与原文直达 → 统一 AI 结构化解读卡（共用 `brief` 与 `news`）→ 统一正文 / README / 机翻 / 图集 / 版本修订 Diff，并优化窄屏标题与外链按钮排布。
4. **复核薄壳组件性价比**
   - 确认 `SignalHero`（3 处复用）、`BarDistributionCard`（2 处复用）、`SourceHealthMini`（2 处复用）与 `MarkdownBlocks`（详情页 2 处调用）均有实际去重价值，未引入新薄壳组件。

---

### P2：存量超大文件清单（按需渐进处理，不强行一次大改）

以下文件属于早期多轮功能演进累积，记录在此作为后续改动触达时的优先整理对象：

| 层级 | 文件路径 | 当前行数 | 主要成因 |
| :--- | :--- | ---: | :--- |
| 前端页面 | `web/app/pages/admin/targets.vue` | 1,576 | 汇聚全平台采集目标表单、分组与状态操作 |
| 前端页面 | `web/app/pages/calendar.vue` | 974 | 活动日历多视图（时间轴/月历/列表）与筛选逻辑集中 |
| 前端页面 | `web/app/pages/admin/users.vue` | 708 | 账号列表、权限、邀请码与订阅明细集中在单页 |
| 前端组件 | `web/app/components/account/Monitors.vue` | 632 | 多类型定制监控创建表单与规则管理合一 |
| 后端接口 | `server/app/api/public.py` | 2,154 | 公开站全部接口（事件、内容、日历、趋势、AI、项目）集中在单路由文件 |
| 后端接口 | `server/app/api/admin.py` | 1,716 | 管理端全量接口集中在单文件 |
| 后端服务 | `server/app/services/mcp_tools.py` | 1,467 | 全部只读 MCP 工具与序列化集中在单文件 |

---

## 4. 后续执行的硬约束与验收标准

1. **度量真实减重，拒绝“换壳增行”**：每次收束提交单独核算「剔除死文件后的活跃代码净行差」与文件数变化，目标必须明确负增长或显著降低圈复杂度。
2. **线上数据安全先行**：若裁剪任何平台或目标类型（如 `gitcode` / `gh_issues`），须先确认数据库存量表（`targets`、`custom_feed_sources`、`raw_contents`）无活跃记录或做好兼容降级，不破坏历史档案读取。
3. **确定性验证闭环**：
   - 后端全量单测：`uv run --directory server python -m pytest -q`
   - 前端单测与生产构建：`npm --prefix web test && npm --prefix web run build`
   - 模板改动必须完成浏览器真机回归（游戏频道、AI 快讯、开源趋势与详情页、小黑盒主题流），核对深浅主题、壁纸毛玻璃、响应式无横向溢出与控制台零报错。
