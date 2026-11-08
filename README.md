<div align="center">

<img src="web/public/icon.png" width="88" height="88" alt="AniSignal 标志">

# AniSignal

**让重要的变化，主动找到你。**

自托管的游戏资讯与周边追踪信号台。把官号、官网公告、视频和社区里的公开更新汇到一处，<br>
自动归并成有出处可查的事件，再按你的关注送到网页、邮件和 QQ。

<p>
<a href="https://github.com/tianyin231/AniSignal/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/tianyin231/AniSignal/ci.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI"></a>
<a href="LICENSE"><img alt="License: AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-blue?style=flat-square"></a>
<img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white">
<img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white">
<img alt="Nuxt 4" src="https://img.shields.io/badge/Nuxt-4-00DC82?style=flat-square&logo=nuxt&logoColor=white">
<img alt="Docker Compose" src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white">
</p>

**[在线体验](https://anisignal.xyz/)** · [交互演示](https://anisignal.xyz/intro) · [快速开始](#快速开始) · [部署指南](docs/deployment.md) · [更新记录](https://anisignal.xyz/project) · [反馈问题](https://github.com/tianyin231/AniSignal/issues)

**简体中文** · [English](README.en.md)

</div>

<a href="https://anisignal.xyz/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/readme/home-dark.webp">
    <img src="docs/assets/readme/home-light.webp" alt="AniSignal 首页：今日信号、即将到来的活动与进行中的活动" width="100%">
  </picture>
</a>

关注一款游戏的版本、一件周边的发货，往往要在官号、官网、视频和社区之间来回翻，还要分辨哪些是营销号转载。AniSignal 替你盯着这些公开来源：同一件事的零散消息合并成一条事件，每条消息都标明出处；周边从开订一路追到实物开箱；你关心的关键词有新动静，会主动推送给你。

## 功能一览

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/assets/readme/event.webp" alt="事件详情页：关键事实、当前结论与证据链">
      <p><b>事件追踪</b><br>官号、视频与转载里说的是同一件事时自动归并，保留证据链、首见时间和修订记录，结论附可信度。</p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/assets/readme/products.webp" alt="商品追踪页：周边卡片上的追踪状态、价格与开箱账号数">
      <p><b>周边履约</b><br>从官方公告里识别周边商品，持续追踪尾款、发货、实物开箱和质量反馈，确认到货后自动结束追踪。</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/assets/readme/calendar.webp" alt="活动日历：按游戏汇总的活动开始、截止与维护时间">
      <p><b>活动日历</b><br>从公告里提取活动、卡池、抽奖的开始与截止时间，按游戏筛选，可一键加入自己的日历。</p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/assets/readme/mobile.webp" alt="手机端界面：首页、周边详情与活动日历">
      <p><b>手机友好</b><br>底部导航、触控尺寸与浅色 / 深色主题都按手机重新排版，打开链接就能看。</p>
    </td>
  </tr>
</table>

此外还有：

- **多平台采集**：B 站官号动态与视频、YouTube、X、游戏官网公告与 RSS；每个来源单独记录健康状态，失败自动退避。
- **关注与推送**：站内关键词关注，或创建独立的 B 站 UID / 关键词监控；每天 08:00 邮件早报，重要消息即时提醒。
- **QQ 机器人**：配套 AstrBot 插件，支持 QQ 官方群与 NapCat，推送图文简报，也能在群里直接查询。
- **AI 快讯**：模型发布与行业动态经过两轮质量评分、中文翻译后归组，与游戏分区分开浏览。
- **管理后台**：`/admin` 管理信源、审核、分类规则、用户与邮件广播，模型调用费用与 Token 用量实时记账。

## 支持的分区

目前覆盖 14 个游戏分区与 AI 快讯，可以单独进入某个游戏，也可以在首页看全部。

| 分区 | 主要信源 |
| :--- | :--- |
| 鸣潮 | B 站官号、库洛官网公告、YouTube、社区高播放视频 |
| 原神 · 明日方舟：终末地 | B 站官号、官网公告、X、YouTube、社区视频（原神另含米游社） |
| 崩坏：星穹铁道 · 明日方舟 · 碧蓝航线 | B 站官号、YouTube、社区视频 |
| 胜利女神：NIKKE | 官网公告、X、YouTube |
| 绝区零 · 无限暖暖 · 星布谷地 · 崩坏：因缘精灵 · 洛克王国：世界 · 蓝色星原：旅谣 · 异环 | B 站官号、社区高播放视频 |
| AI 快讯 | RSS 新闻源、X 上的 AI 相关账号、B 站搜索 |

信源在后台随时增减，以上为公开站点当前的配置。

## 架构

```mermaid
flowchart LR
    S["公开信源<br/>B 站 · YouTube · X<br/>官网公告 · RSS"] --> Q["采集调度<br/>arq + Redis"]
    Q --> R[("原始内容<br/>MySQL")]
    R --> E["事件归并<br/>证据链 · 修订史"]
    R --> P["周边追踪<br/>开箱 · 质量反馈"]
    R --> A["AI 审核与摘要<br/>用量记账"]
    E --> W["Web 信号台<br/>Nuxt 4 SSR"]
    P --> W
    A --> W
    E --> M["邮件早报<br/>即时提醒"]
    A --> M
    E --> B["AstrBot 插件<br/>QQ 群 · 私聊"]
    A --> B
```

后端是 FastAPI + SQLAlchemy（异步）+ MySQL 8.4，任务队列用 Redis / arq；前端是 Nuxt 4 SSR + Tailwind CSS 4。生产环境由 Docker Compose 一键启动，本地开发可以只用 SQLite。

## 快速开始

### 生产部署（Docker Compose）

环境要求：Git、Docker 24+ 与 Docker Compose v2。

```bash
git clone https://github.com/tianyin231/AniSignal.git
cd AniSignal
cp .env.example .env
```

编辑 `.env`：

- 把 `ANISIGNAL_ADMIN_TOKEN` 设为一串随机长字符串（管理后台的访问令牌）；
- 设置 `MYSQL_ROOT_PASSWORD` 与 `MYSQL_PASSWORD`；
- 按需调整对外监听端口（默认 `WEB_PORT=127.0.0.1:8090`）。

启动：

```bash
docker compose up -d --build
docker compose ps
```

打开 **`http://127.0.0.1:8090`** 即可浏览。进入 `/admin` 填入刚才的管理令牌，就可以开始配置信源、规则与 AI。

### 本地开发

本地开发时后端可以直接用 SQLite 和进程内队列，不需要 MySQL 和 Redis：

```bash
# 后端（Python 3.12+ / uv）
cd server
uv run python -m app.local

# 前端（Node.js 22 / npm，与构建镜像一致）
cd web
npm ci
npm run dev
```

完整配置与环境变量说明见[部署与本地开发文档](docs/deployment.md)。

## 接入 QQ（AstrBot 插件）

插件位于 `integrations/astrbot_plugin_anisignal`：

1. 在 AstrBot 管理面板的「插件管理 → 从链接安装」中填入本仓库地址 `https://github.com/tianyin231/AniSignal`；
2. 填写 AniSignal 服务地址与授权密钥；
3. 在群聊或私聊里发送 `/情报订阅` 或 `/帮助`，设置要接收的分区和推送方式。

支持每日早报、重要消息即时推送、图文简报、沿用会话人格和关键词查询，详见[插件使用说明](integrations/astrbot_plugin_anisignal/README.md)。

## 文档

- [功能与架构指南](docs/features.md)：三种关注方式、数据幂等与事件聚合原理
- [部署与运维手册](docs/deployment.md)：容器编排、服务器更新流程与代理配置
- [后端说明](server/README.md)：FastAPI 结构、Alembic 迁移与采集器接口
- [前端说明](web/README.md)：Nuxt 4 目录结构与信号台设计令牌

反馈问题时，请附上访问路径、复现步骤、部署方式和脱敏后的日志。

## 许可证

Copyright © 2026 tianyin231 与贡献者。本项目以 [GNU AGPL-3.0](LICENSE) 发布：可以自由使用、修改和分发；修改后的版本如果通过网络向他人提供服务，也需要以 AGPL-3.0 公开完整源码。

`integrations/aihot` 收录的是第三方项目 [AIHOT](https://github.com/KKKKhazix/AIHOT)，按其原有的 MIT 许可证分发（见该目录下的 `LICENSE` 与 `NOTICE`），“AIHOT”名称与标志不在许可范围内。

## 致谢

本项目由人类开发者与多款大语言模型结对迭代构建。

<details>
<summary>协同模型与开发工具</summary>

| 研发机构 | 协同模型 |
| :--- | :--- |
| **Anthropic** | `Claude Opus 5.5` |
| **OpenAI** | `GPT-6.1 Sol` · `GPT-6 Astra` · `GPT-5.6 Sol` |
| **Google DeepMind** | `Gemini 3.8 Flash` |
| **xAI** | `Grok 4.7` |
| **StepFun** | `Step 5 Preview` |
| **Zhipu AI** | `GLM-5.3` · `GLM-5.3 Flash` |
| **Xiaomi** | `MiMo v2.6 Pro` |
| **Agnes AI** | `agnes-3.0-flash` · `agnes-2.0-flash` |

开发工具与 Agent 宿主：`ChatGPT(codex)` · `Claude Code` · `ZCode` · `Antigravity` · `GrokBot` · `Muse` · `GPTDots`

</details>

<div align="center">

<a href="https://github.com/tianyin231/AniSignal/graphs/contributors"><img src="https://contrib.rocks/image?repo=tianyin231/AniSignal" alt="贡献者"></a>

<a href="https://star-history.com/#tianyin231/AniSignal&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=tianyin231/AniSignal&type=Date&theme=dark">
    <img alt="Star History" src="https://api.star-history.com/svg?repos=tianyin231/AniSignal&type=Date" width="600">
  </picture>
</a>

</div>
