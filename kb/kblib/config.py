"""全局路径与常量。"""

from __future__ import annotations

import os
from pathlib import Path

# kb/ 目录（本文件位于 kb/kblib/config.py）
KB_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = Path(os.environ.get("KB_DATA_DIR", KB_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
SPIKE_DIR = RAW_DIR / "spike"
KB_DB = DATA_DIR / "kb.db"
CHUNKS_PATH = DATA_DIR / "chunks.jsonl"
EVAL_DIR = KB_ROOT / "eval"
QUESTIONS_PATH = EVAL_DIR / "questions.jsonl"

WIKI_BASE = "https://wiki.biligame.com/blhx"
API_URL = f"{WIKI_BASE}/api.php"
USER_AGENT = "anisignal-kb/0.1 (offline knowledge-base build; polite 1.5 req/s)"

# 抓取礼貌参数
DEFAULT_RPS = 1.5          # 每秒请求数上限
BATCH_TITLES = 20          # 单批标题数：BWIKI 有单篇几 MB 的大页，一批里混进一个
                           # 就能把整个响应拖到超时；20 比较稳，失败了也有二分兜底
FETCH_TIMEOUT = 180        # 单次 curl 超时（秒）。大页响应慢，90s 不够
FETCH_MAX_RETRIES = 6      # 超时/5xx/429 的重试次数
BACKOFF_CAP = 60           # 指数退避上限（秒）
SHARD_PAGES = 500          # 每个 gzip 分片的页数

# infobox 模板 -> (实体类型, 分类)
# 命名空间 10 下的模板名（不带 "模板:" 前缀）
INFOBOX_TEMPLATES: dict[str, tuple[str, str]] = {
    "舰娘图鉴": ("ship", "ship"),
    "舰炮图鉴": ("equipment", "naval_gun"),
    "防空炮图鉴": ("equipment", "aa_gun"),
    "舰载机图鉴": ("equipment", "aircraft"),
    "鱼雷图鉴": ("equipment", "torpedo"),
    "导弹图鉴": ("equipment", "missile"),
    "设备图鉴": ("equipment", "gear"),
    "特殊兵装图鉴": ("equipment", "special_aux"),
    "指挥喵图鉴": ("cat", "commander_cat"),
}

# 出现即视为导航/样板噪音的模板（切 chunk 时整段丢弃）
NAV_TEMPLATE_PREFIXES = (
    "导航",
    "舰娘图鉴导航",
    "塞壬图鉴导航",
    "塞壬图鉴顶栏",
    "面包屑",
    "图鉴小标题",
    "图鉴模拟",
    "图鉴筛选",
    "装备图鉴筛选",
    "舰娘定位筛选",
    "筛选功能",
    "筛选",
    "轮播图",
    "配色",
    "渐变",
    "字体图标",
    "小图标",
    "头像",
    "展开折叠",
    "折叠面板",
    "选项卡面板",
    "切换标签",
    "施工中",
    "待补充",
    "已过期",
    "允许编辑",
    "免除CC协议",
    "消歧义",
    "视频",
    "B站视频",
    "短视频",
    "狗赛克视频",
    "官方微博",
    "更新日志",
    "公告",
    "文章水印",
    "警告",
    "提示",
    "注意",
    "备注",
    "引用",
    "外部链接",
    "友情链接",
    "友情WIKI",
    "Wiki页面加载检测",
    "资源加载",
    "ResourceLoader",
    "JS",
    "CSS",
    "排版",
    "板块",
    "段落样式",
    "重复",
    "条件项",
    "转化",
    "Math",
    "Frac",
    "Radic",
    "Ruby",
    "注音",
    "META资讯同步",
)

# 切 chunk 时整段丢弃的 section 标题（精确/包含匹配）
NOISE_SECTION_KEYWORDS = (
    "导航",
    "分类",
    "参见",
    "外部链接",
    "参考文献",
    "注释",
    "图鉴筛选",
    "筛选",
    "更新历史",
    "更新日志",
    "公告",
)
