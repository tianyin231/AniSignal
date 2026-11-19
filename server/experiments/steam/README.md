# Steam 采集程序（纯匿名，零凭据）

**独立交付物，未接入 AniSignal 主系统。** 与小黑盒（`experiments/xhh/`）、
TapTap（DL01 `/opt/tt-monitor/`）交付物同构的第三个实例。全部信号匿名可取，
无需登录态、无需 API key、不使用 steamcommunity.com（该域匿名面已被
登录墙/429 惩罚盒双重封锁，且与采集目标无关）。

```
experiments/steam/
├── steam_collect.py   采集主程序：详情 + 在线人数 + SteamSpy + 中文评价（翻页增量）
├── steam_probe.py     健康探针：四端点可用率/延迟
├── db_mirror.py       JSONL -> SQLite（幂等去重）
├── ops_report.py      运维日报
├── deploy/steam-monitor.cron  生产排期
└── samples/           真实响应样本（fixture）
```

## 信号集（2026-11-19 实测）

| 信号 | 端点 | 关键数据 |
|---|---|---|
| 实时在线人数 | `api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/`（**无 key**） | 并发玩家数，版本/卡池热度曲线 |
| 聚合游玩 | `steamspy.com/api.php?request=appdetails` | owners 估算、**average/median_forever 平均/中位游玩时长**、好评率 |
| 中文评价 | `store.steampowered.com/ajaxappreviews/{appid}?json=1&appids={appid}&start_offset=` | 评价全文、推荐/有用/有趣数、**评价者该游戏时长/拥有游戏数/评测数** |
| 游戏详情 | `store.steampowered.com/api/appdetails?appids=` | 价格/开发商/发行日 |

**端点关键细节**：appreviews 的 appid 必须**同时出现在路径和 appids 参数**
（`ajaxappreviews/{appid}?appids={appid}`），仅传参数会 302 跳首页；
`day_range` 参数会导致 500（不要传）；分页为 `start_offset` 偏移，
`num_per_page` 上限 100。

## 限流纪律（已内置）

- 评价 API：150 请求/5 分钟（steamreviews 实测值），本程序默认每轮每游戏
  最多 3 页 × 100 条，请求间 2.5s；
- SteamSpy：请求间隔 ≥4s（其官方要求）；
- 默认每轮 4 游戏 ≈ 24 请求，30 分钟一轮 ≈ 48 req/h，远低于各面限流。

## 已验证（2026-11-19 本地两轮）

```
round 1: 新评价 400（4 游戏 × 100）| 在线/Spy 全通 | 0 错误
round 2: 新评价 0（增量去重生效）
probe:   detail/online/spy/reviews 四面 ok（223-705ms）
```

游戏配置 4 个（AniSignal 社区中在 Steam 有发行的）：鸣潮 3513350 /
绝区零 4162040 / 终末地 4732690 / 无限暖暖 3164330。
崩铁/原神/方舟/碧蓝/NIKKE 不在 Steam（storesearch 实测无结果），
`GAMES` 字典可直接扩充。

## 已知限制（重要）

1. **小黑盒式“个人游玩统计”匿名不可得**：Steam 游戏库 tab
   （`/games/?tab=all`）对匿名返回登录页（公开档案同样验证）；档案主页
   匿名可读但 ~8 请求后 429 惩罚盒。要完整个人统计只有运营者自备 Web API
   key（非登录态，公开数据令牌）一条路——见会话结论，本程序不含；
2. 匿名 per-user 数据仅评价者切片（playtime_at_review / num_games_owned /
   num_reviews），已包含在评价记录里；
3. 未 soak：长期频率曲线与 IP 惩罚盒恢复周期待 DL01 部署后观测。

## 快速开始

```bash
python3 steam_collect.py --once    # 跑一轮
python3 steam_probe.py             # 探针
python3 db_mirror.py stats         # 查库
python3 ops_report.py              # 日报
```

依赖：Python 3.11+ + httpx（唯一第三方依赖）。生产部署见
`deploy/steam-monitor.cron`。

## 许可

自有脚本遵循本仓库许可。Steam/SteamSpy 为第三方服务，本程序仅取其公开
数据，使用风险自担。
