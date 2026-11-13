# NapCat 部署示例（OneBot 通道）

个人 QQ / OneBot 通道参考配置，可与 AstrBot 插件对接（aiocqhttp）。

## 目录结构

```
./napcat/
  docker-compose.yml
  .env
  qq/          # QQ 配置与登录态（持久化，勿删）
  napcat/      # NapCat 配置
```

## 网络

- `astrbot_net`：与 AstrBot 及 AniSignal 同网（可通过 `ASTRBOT_NETWORK` 指定已有外部网络名），反向 WS 可连 `ws://astrbot:6185/ws`

## 端口（默认仅本机）

| 端口 | 用途 |
| --- | --- |
| 127.0.0.1:6099 | NapCat WebUI（扫码登录、配 OneBot） |
| 127.0.0.1:3001 | OneBot HTTP/WS（可选） |

远端服务器访问 WebUI：使用 SSH 本地端口转发 `ssh -L 6099:127.0.0.1:6099 user@your-server`，浏览器打开 `http://127.0.0.1:6099`。

## 启动

```bash
cd deploy/napcat
docker network create anisignal_astrbot 2>/dev/null || true
APP_PATH=$PWD docker compose up -d
docker compose logs -f napcat
```

## 登录后接 AstrBot

1. WebUI 扫码登录备用号
2. OneBot11 → 反向 WS → `ws://astrbot:6185/ws`（以 AstrBot 面板 aiocqhttp 配置为准，可能带 path/token）
3. AstrBot 面板添加 aiocqhttp 平台，与 NapCat 对齐
4. 群里发送 `/情报会话` 获取 UMO，或在插件面板完成订阅配置

## 风控注意

- 一号少群、固定出口、开启静默时段、控制单次配图数量
- 勿使用主力账号；配置目录 `qq/` 必须持久化，避免频繁重登
