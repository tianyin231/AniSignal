# AniSignal 情报助手（DeepSeek Harness 插件）

`dsh-plugin-anisignal` 是 [AniSignal](https://github.com/tianyin231/AniSignal) 的原生 DeepSeek Harness（`dsh`）伴侣插件，为通过 `dsh` 接入 QQ 机器人的群聊与私聊提供二游 / AI 分区订阅、每日速报卡片、即时快讯、活动日历提醒、商品发货证据检索与个人定时提醒。

## 兼容的 QQ 接入通道

本插件同时支持 DSH 生态中的两类主流 QQ Bot 接入方式，并可在同一实例内混用：

| 通道前缀 | 适配插件 | 主动推送方式 |
| --- | --- | --- |
| `dsh_qqbot:GroupMessage:<groupOpenid>`<br>`dsh_qqbot:FriendMessage:<userOpenid>` | 腾讯官方 `@tencent-connect/dsh-qqbot` | 自动读取 DSH Profile 中的官方 `AppID` / `AppSecret`，通过 QQ 开放平台 OpenAPI v2 推送图文与情报笺 |
| `dsh_onebot:GroupMessage:<群号>`<br>`dsh_onebot:FriendMessage:<QQ号>` | 社区 `dsh-onebot-qq`、`dsh-qq-onebot`（NapCat / LLOneBot 等） | 自动读取 `dsh-qq` / `onebot` 正向 WebSocket 配置，或使用显式配置的 OneBot HTTP / WS 地址，并在反向 WS 场景支持活跃工具兜底 |

> 提示：若 OneBot 侧采用**反向 WebSocket**（NapCat 主动连入 `dsh-onebot-qq`），建议在 NapCat 中同时开启 HTTP 服务并在插件高级参数中填入 `http://127.0.0.1:3000`，以保证宿主刚冷启动、尚未收到群消息时也能准点推送早报。

## 安装方式

### 方式一：通过 GitHub 仓库安装

在 DSH 插件管理中添加仓库地址，或在目标 Profile 目录下安装：

```bash
# 从 GitHub 安装
pnpm add https://codeload.github.com/tianyin231/DSH_Plugin_AniSignal/tar.gz/refs/heads/main
```

并在当前 Profile 的 `cordis.patch.yml` 中启用（若通过 DSH 桌面端插件市场安装则自动写入）：

```yaml
- id: anisignal
  path: dsh-plugin-anisignal
  config: {}
```

### 方式二：开启早报图片卡渲染（可选）

插件采用零浏览器、纯 Node 轻量级 SVG → PNG 渲染方案。若环境中安装了可选依赖 `@resvg/resvg-js` 且存在中文字体（macOS 自带苹方、Windows 自带微软雅黑、Linux 安装 `fonts-noto-cjk` 或指定字体路径），每日早报会自动生成 `960px` 宽度的「AniSignal 情报笺」PNG 卡片；若未安装或缺少中文字体，将自动无感降级为纯文本早报。

```bash
# Linux 服务器补充中文字体示例
sudo apt-get install -y fonts-noto-cjk
```

## 快速上手

1. **获取用户 Token**：登录 [anisignal.xyz](https://anisignal.xyz)（或你的自建节点），在「账号中心」生成一枚 **Bot 用户 Token**。
2. **填写插件配置**：打开 DSH **设置 → AniSignal 情报助手**（或在插件详情页点击「设置」），填入用户 Token 并点击「保存并同步」。
3. **添加 QQ 会话**：
   - 在已接入机器人的 QQ 群或私聊里发送一句消息（例如：「查看当前情报会话标识」），机器人会调用 `anisignal_session_info` 返回当前会话的 `UMO` 与你的发言人标识；
   - 或者直接在设置页「从已发现的 QQ 会话一键添加」列表中点击对应会话，勾选启用、选择关注的分区与早报时间后点击「保存并同步」。

## 注册的模型工具

仅在已注册且启用的会话中注入上下文说明（`anisignal_session_info` 除外，可在任意会话用于辅助绑定）：

- `anisignal_query`：按会话授权范围查询 `digest`（24h 速报）、`news`（7 日资讯）、`trending`（热度飙升）、`calendar`（活动与维护日程）、`codes`（兑换码）、`products`（商品发货/到货核验）、`revisions`（官方公告修订/抓暗改）。
- `anisignal_expand`：按最近一次早报或通知编号（1–3）展开完整来源细节。
- `anisignal_subscription`：查看或口述修改当前会话订阅（群聊需开启「允许聊天改订阅」并匹配管理用户标识）。
- `anisignal_reminder`：在当前会话创建、查看或取消本人的定时提醒。
- `anisignal_session_info`：查询当前 QQ 会话的 `UMO`、发言人标识与节点同步状态。

## 开发与测试

```bash
node --test test/*.test.mjs
```

## 开源许可

与 AniSignal 主仓库一致，采用 [AGPL-3.0-or-later](./LICENSE) 协议发布。
