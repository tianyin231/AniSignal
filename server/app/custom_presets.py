"""站点维护的定制信息源；与游戏分区及用户个人监控配置分开。"""

CUSTOM_PRESETS = {
    "nikke_global": {
        "platform": "game_official", "kind": "notices", "target_value": "nikke_global",
        "name": "NIKKE 国际服",
        "target_label": "NIKKE 国际服官网（英文）",
        "description": "国际服官方更新、维护、活动和已知问题公告，保留原始发布时间与英文原文链接。",
        "source_url": "https://nikke-en.com/news.html",
    },
    "nikke_youtube": {
        "platform": "youtube", "kind": "channel_rss", "target_value": "UCmmgZwsqQRD6PKeo87uq1bA",
        "name": "NIKKE 国际服 · 官方 YouTube",
        "target_label": "YouTube 官方频道 @NIKKEEN",
        "description": "国际服官方预告、活动视频与直播，保留原始发布时间和 YouTube 原文链接。",
        "source_url": "https://www.youtube.com/@NIKKEEN",
    },
    "nikke_x": {
        "platform": "x", "kind": "account_dynamics", "target_value": "nikke_en",
        "name": "NIKKE 国际服 · 官方 X",
        "target_label": "X 官方账号 @NIKKE_en",
        "description": "国际服官方社交动态、活动与奖励通知，保留原帖时间和链接；低频读取并遵守平台冷却。",
        "source_url": "https://x.com/NIKKE_en",
    },
}


def custom_preset(platform: str, kind: str, target_value: str) -> dict:
    return next((item for item in CUSTOM_PRESETS.values()
                 if (item["platform"], item["kind"], item["target_value"]) == (platform, kind, target_value)), {})
