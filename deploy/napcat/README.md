# NapCat 部署（宝塔 Docker）

个人 QQ / OneBot 通道，与 AniSignal 现有 AstrBot 插件对接（aiocqhttp）。

## 路径

```
/www/dk_project/dk_app/napcat/napcat_nNjs/
  docker-compose.yml
  .env
  qq/          # QQ 配置与登录态（持久化，勿删）
  napcat/      # NapCat 配置
```

## 网络

- `baota_net`：与 AstrBot 同网，反向 WS 可连 `http://astrbot_nNjs:6185`
- `ap_notify_net`：与 AniSignal app/collector 同网

## 端口（默认仅本机）

| 端口 | 用途 |
| --- | --- |
| 127.0.0.1:6099 | NapCat WebUI（扫码登录、配 OneBot） |
| 127.0.0.1:3001 | OneBot HTTP/WS（可选对外） |

外网访问 WebUI：SSH 隧道 `ssh -L 6099:127.0.0.1:6099 us3q`，浏览器开 `http://127.0.0.1:6099`。

## 启动

```bash
cd /www/dk_project/dk_app/napcat/napcat_nNjs
docker compose up -d
docker compose logs -f napcat_nNjs
```

## 登录后接 AstrBot

1. WebUI 扫码登录**小号**
2. OneBot11 → 反向 WS → `ws://astrbot_nNjs:6185`（以 AstrBot 面板 aiocqhttp 配置为准，可能带 path/token）
3. AstrBot 面板添加 aiocqhttp 平台，与 NapCat 对上
4. 群里 `/情报会话` 拿 UMO → AniSignal `/admin/bot` 建会话

## 风控注意

- 一号少群、固定 IP、收紧 merge/hourly_limit、严格静默、单图
- 勿用主号；配置目录 `qq/` 必须持久化，避免反复扫码
