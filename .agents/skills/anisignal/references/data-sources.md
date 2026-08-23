# 已验证数据源与采集方案

来源：2026-08-13 在美国服务器 `154.40.58.230` 上的匿名实测（原始笔记位于 `.worktrees/platform-probe/docs/platform-integration-notes.md`，未入 git，本文件是其唯一持久副本）。所有测试不使用账号、Cookie、代理或验证码绕过。

写采集代码时以本文件为准，不要凭记忆改接口。

## 数据源总览

| 数据源 | 链路 | 匿名结果 | 首版决策 |
|---|---|---|---|
| B 站 | `ghcr.io/tamnd/bili:latest` Docker CLI | 成功 | 接入（核心） |
| 米游社 | `searchPosts -> getPostFull` 公开接口 | 成功 | 接入 |
| 鸣潮国服官网 | 库洛公开公告 CDN JSON | 成功 | 接入 |
| 鸣潮国际服官网 | 库洛公开公告 CDN JSON | 成功 | 接入 |
| YouTube | 官方 RSS（搜索可用 yt-dlp） | 成功 | 低成本补充 |
| 库街区 | `Wuthery/kuro.py` 公告接口 | 只能拿官方公告；普通帖子是前端渲染 | 只接官方公告 |
| 小黑盒 | 无维护中的可用匿名项目 | 未确认 | 不接入 |
| X | twikit / twscrape | 需登录或 Cookie | 不接入 |
| 小红书 | xiaohongshu-cli | 需 Cookie | 不接入 |
| 抖音 | Douyin_TikTok_Download_API | 需浏览器 Cookie | 不接入 |
| 森空岛 | Skland_API | 需凭证，项目停维 | 不接入 |

## B 站：bili-cli（核心方案）

镜像与版本：

```text
ghcr.io/tamnd/bili:latest
bili 0.2.0 (24ecf1a) built 2026-06-15T12:53:44Z
```

匿名状态已验证（`isLogin: false` 即可正常工作）。`/data` 卷用于响应缓存；验证真实请求加 `--no-cache`。

统一调用形式：

```bash
docker run --rm -v /www/bilibili-cli-probe/data:/data ghcr.io/tamnd/bili:latest <command>
```

### 已验证命令

```bash
# 搜索视频（单次 1.7–2.0 秒，实测连续 5 次全部成功）
search '鸣潮' --type video -n 5 -o json --no-cache
search '爱弥斯键盘' --type video -n 5 -o json --no-cache

# 视频详情（搜索结果统计字段不全，最终数据以详情为准）
video BV1Mcgp6ME1x -o json --no-cache

# 用户主页
user 1955897084 -o json --no-cache

# 用户发布的视频
user 1955897084 --videos -n 20 -o json --no-cache

# 用户动态：正确入口是独立的 dynamics 命令
dynamics 1955897084 -n 20 -o json --no-cache

# 单条动态详情
dynamic 1233796718099693587 -o json --no-cache
```

**陷阱**：`user <mid> --dynamics` 这个入口返回空数组，判断动态可用性必须用独立的 `dynamics <mid>` 命令。

### 视频返回字段（搜索与详情共用）

```json
{
  "bvid": "BV1Mcgp6ME1x",
  "aid": 117088291265434,
  "cid": 40878802947,
  "title": "视频标题",
  "description": "视频简介",
  "owner_mid": 29769961,
  "owner_name": "UP主名称",
  "view_count": 680,
  "danmaku_count": 0,
  "reply_count": 14,
  "favorite_count": 3,
  "coin_count": 6,
  "share_count": 1,
  "like_count": 26,
  "pubdate": 1786625704,
  "pubdate_text": "2026-08-13 12:55:04",
  "duration_seconds": 251,
  "cover_url": "https://i1.hdslb.com/bfs/archive/example.jpg",
  "url": "https://www.bilibili.com/video/BV1Mcgp6ME1x",
  "tags": ["鸣潮", "爱弥斯"],
  "pages": []
}
```

可满足：BV/AV 号、标题简介、原始链接、封面、UP 主信息、播放/点赞/评论/收藏/投币/分享/弹幕数、发布时间、时长、分 P、标签。**采集模式固定为：搜索拿 BV 号 → `video` 详情作为最终数据。**

### 用户主页字段

```json
{
  "mid": 1955897084,
  "name": "鸣潮",
  "sign": "鸣潮往复 文明不屈",
  "level": 6,
  "face_url": "头像地址",
  "official_role": 3,
  "official_title": "鸣潮官方账号",
  "follower_count": 5344114,
  "following_count": 11
}
```

### 动态字段

```json
{
  "id": "1235883715731652807",
  "type": "DYNAMIC_TYPE_ARTICLE",
  "author_mid": 1955897084,
  "author_name": "鸣潮",
  "pub_ts": 1786590001,
  "pub_text": "10小时前",
  "text": "",
  "pics": [],
  "video_bvid": "",
  "stat_like": 7110,
  "stat_reply": 1714,
  "stat_forward": 49
}
```

可拿到：动态 ID/类型、作者、发布时间、正文文本字段、图片列表、关联视频 BV 号、点赞/评论/转发数。**已知限制：部分图文/文章动态的 `text` 为空，不能假设每条正文都已展开**；需要完整正文时要针对动态类型补充解析。

### 官方账号 UID

鸣潮官方账号有效 UID 为 **1955897084**（`official_title: 鸣潮官方账号`）。旧 UID `1955897080` 已 404。UID 必须配置化，禁止硬编码。

### 外链播放器（不下载、不代理视频流）

```html
<iframe src="https://player.bilibili.com/player.html?bvid=BV1Mcgp6ME1x&autoplay=0&danmaku=0"
        scrolling="no" frameborder="0" allowfullscreen></iframe>
```

多 P 用 `p` 或 `cid` 参数。容器建议 16:9。

### 适配器接口建议

```text
search_videos(keyword, limit)
get_video(bvid)
get_user(mid)
get_user_videos(mid, limit)
get_user_dynamics(mid, limit)
get_dynamic(dynamic_id)
build_player_url(bvid, cid=None, page=1)
```

## 米游社

公开 `searchPosts -> getPostFull` 链路（实测连续 5 次 retcode=0，每次 10 条）：

```text
搜索： https://bbs-api.miyoushe.com/post/wapi/searchPosts?keyword=胡桃&gids=2&page=1&page_size=20
详情： https://bbs-api.miyoushe.com/post/wapi/getPostFull?post_id={post_id}
```

- `gids=2` 是原神；关键词暂以"胡桃"测试。
- 详情可拿 HTML 正文、作者、原图、话题、浏览/评论/点赞/收藏/转发数。
- 部分完整文章接口校验请求头，以实际返回状态为准，保留低频、缓存、重试。

建议封装：`search_posts(keyword, gids, page, page_size)`、`get_post(post_id)`；评论接口未来确需再启。

## 鸣潮官网公告（国服 + 国际服）

库洛公开 CDN，无需登录，推荐参考 `Wuthery/kuro.py` 的 `get_game_announcements()` / `get_game_announcement_details()`：

```text
国服列表：  https://aki-gm-resources-back.aki-game.com/gamenotice/G152/76402e5b20be2c39f095a152090afddc/notice.json
国服详情：  .../content/G152/.../{notice_id}/zh-Hans.json   （实测 24 条公告）

国际服列表：https://aki-gm-resources-back.aki-game.net/gamenotice/G153/6eb2a235b30d05efd77bedb5cf60999e/notice.json
国际服详情：.../content/G153/.../{notice_id}/en.json        （实测 18 条公告）
```

**实测响应形状（2026-08-22，AniSignal 采集器实测入库 16 条验证）**：

- 列表：`{"game": [公告项, ...]}`——包裹键是 `game`，不是常见的 list/data/items；
- 列表项：`id`（字符串）、`tabTitle` / `tabBanner` 为多语言 dict（键如 `zh-Hans` / `en`），且 `tabBanner` 的语言值是**多尺寸 URL 列表**；
- 时间：`startTimeMs` / `endTimeMs` / `operateTimeMs`，**毫秒**时间戳；
- 详情：`{"noticeId": 数字, "textContent": "HTML 正文"}`，标题只在列表项的 `tabTitle` 里；
- `contentPrefix` 含多个 CDN 备用地址；同一公告可能多栏目重复挂载（不同 id 同标题），去重归事件引擎，采集层按 id 各存一份。

解析实现见 `server/app/parsers/kuro.py`（PARSER_VERSION = "2"）。

## YouTube

官方频道更新用 RSS：`https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}`。关键词搜索与元数据可用 `yt-dlp`；首版只存视频 ID、标题、发布时间、缩略图、原链，不下载视频。

## 库街区边界

`kuro.py` 可稳定拿库洛公告；普通帖子页面是前端应用壳，HTTP 200 不等于拿到内容。首版只承诺官方公告，不承诺社区帖子。

## 已知限制与资源观察

1. B 站官方动态旧接口直连返回 412 验证页，旧自写 HTTP 动态链路不可用。
2. bili-cli 匿名链路受平台风控影响，必须保留低频、缓存、重试。
3. 视频可能被删除/私密/付费/关闭外链播放，采集端无法修复，只能记录状态。
4. 24 小时探针实测未跑满（800/984）：服务器内存峰值 4.79/7.8 GB，Chromium RSS 峰值 2.96 GB、进程峰值 13 个。**结论：常态轮询优先 HTTP/JSON 与专用 CLI，浏览器只作补充**，且每平台单独记录最近成功时间与连续失败次数。

## 禁止事项

绕过验证码或风控、破解登录态、伪造或盗用 Cookie/token、绕过访问控制、使用攻击手段规避平台封禁——均不属于本项目的采集方案，任何代码与讨论中都不执行、不建议。
