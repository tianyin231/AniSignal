"""头像预设库：key → 展示信息。前后端共享同一份 key 白名单。

设计约定：全站无紫色系（README/web 约定），渐变组合避开 purple/violet。
"""

AVATARS: list[dict] = [
    {"id": "aurora-blue", "label": "极光蓝", "gradient": "from-blue-400 to-blue-600"},
    {"id": "sky-cyan", "label": "晴空", "gradient": "from-sky-400 to-cyan-500"},
    {"id": "teal-mint", "label": "薄荷", "gradient": "from-teal-400 to-emerald-500"},
    {"id": "forest", "label": "森绿", "gradient": "from-emerald-500 to-green-600"},
    {"id": "amber-sun", "label": "暖阳", "gradient": "from-amber-400 to-orange-500"},
    {"id": "coral", "label": "珊瑚", "gradient": "from-orange-400 to-rose-500"},
    {"id": "rose-petal", "label": "蔷薇", "gradient": "from-rose-400 to-pink-500"},
    {"id": "crimson", "label": "绯红", "gradient": "from-red-400 to-red-600"},
    {"id": "slate-stone", "label": "岩灰", "gradient": "from-slate-400 to-slate-600"},
    {"id": "midnight", "label": "夜航", "gradient": "from-slate-700 to-zinc-900"},
    {"id": "wave-navy", "label": "浪涌", "gradient": "from-cyan-500 to-blue-700"},
    {"id": "meadow", "label": "草甸", "gradient": "from-lime-400 to-teal-500"},
]

AVATAR_IDS = {item["id"] for item in AVATARS}

DEFAULT_AVATAR = "aurora-blue"
