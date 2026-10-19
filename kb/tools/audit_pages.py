"""v3 覆盖率审计：扫原始分片，逐页统计解析覆盖率与内容类型分布。

输出三张表：
  1. 内容类型分布（other 16287 拆解）
  2. 解析丢失疑点：wikitext 很长但 chunk 数为 0 的页
  3. 特殊语法覆盖：tabber / gallery / 图片引用 / infobox 第 10 种 / 语音台词

用法：./.venv/bin/python tools/audit_pages.py
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kblib import cleantext  # noqa: E402
from kblib.config import (INFOBOX_TEMPLATES, INFOBOX_TEMPLATE_PREFIXES,  # noqa: E402
                          LEGACY_INFOBOX_TEMPLATE_NAMES, RAW_DIR)
from parse import TEMPLATE_KINDS  # noqa: E402

STAGE_RE = re.compile(r"^\d+-\d+(Hard)?$|^\d+-SOS$|^SP\d*$|^EX\d*$|^[A-Z]{2}\d*$")

# 标题模式 → 内容类型（按顺序第一个命中的生效）
TITLE_RULES = [
    ("指挥官档案", re.compile(r"^指挥官")),
    ("同人角色", re.compile(r"^同人角色")),
    ("同人作品", re.compile(r"^同人文|^同人馆")),
    ("测试/沙盒", re.compile(r"^测试|^沙盒|^示例|^playground", re.I)),
    ("用户页", re.compile(r"^用户页")),
    ("编辑教程", re.compile(r"^编辑教程|^碧蓝航线WIKI编辑教程")),
    ("前方情报", re.compile(r"^前方情报")),
    ("敌舰/塞壬", re.compile(r"^敌舰|^塞壬|^仲裁者|^测试者|^公海舰队")),
    ("大型作战/META", re.compile(r"^大型作战|^META|^信标|^舰队科技|^指挥喵|^METAboss攻略要点")),
    ("碧蓝回忆录(剧情)", re.compile(r"^碧蓝回忆录")),
    ("模拟器/小游戏", re.compile(r"^舰队模拟器|^建造模拟器|^小游戏|^试作型|JUUs动态|^宿舍计划|^岛屿计划")),
    ("装备研发", re.compile(r"^装备研发|^研发")),
    ("换装/外观", re.compile(r"^换装|^外观|^皮肤|^誓约系统|^情人节礼物")),
    ("活动/直播/专题", re.compile(r"^活动|^复刻|^庆典|^演习|^联动|^专题|^生放送|^日服公式|^已击毁的往期卫星|^碧蓝海事局")),
    ("世界巡游", re.compile(r"^世界巡游")),
    ("聊天/动态", re.compile(r"^聊天")),
    ("舰娘志/杂志", re.compile(r"^碧蓝舰娘志|^碧蓝航线Crosswave|^碧蓝航线WIKI天梯榜")),
    ("攻略", re.compile(r"攻略|指南|配装|推荐|思路|解析|测评|对比|开荒")),
    ("关卡", re.compile(r"^\d")),
]

# 需要单独点名的语法特征（正文里出现即计数）
FEATURES = {
    "tabber标签": re.compile(r"<tabber|{{\s*#?\s*tag\s*:\s*tabber|选项卡面板|切换标签|tabber"),
    "gallery图库": re.compile(r"<gallery", re.I),
    "图片引用": re.compile(r"\[\[\s*(?:文件|檔案|档案|File|Image)\s*:", re.I),
    "语音台词标题": re.compile(r"语音|台词|语音台词"),
    "折叠面板": re.compile(r"折叠|展开|collapse", re.I),
    "彩色文字": re.compile(r"<span\s+style|{{\s*颜色", re.I),
    "时间轴": re.compile(r"<timeline|easy-timeline", re.I),
    "数学公式": re.compile(r"<math|{{\s*#?tag\s*:\s*math", re.I),
    "SMW查询": re.compile(r"{{\s*#ask|{{\s*#show|{{\s*#set", re.I),
    "雷达图/图表": re.compile(r"{{\s*图表|{{\s*雷达|{{\s*chart", re.I),
}


def classify(title: str, wikitext: str) -> str:
    for name, rx in TITLE_RULES:
        if rx.search(title):
            return name
    return "未归类"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=RAW_DIR)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "audit_pages.json")
    args = ap.parse_args()

    infobox_names = set(INFOBOX_TEMPLATES)
    kinds = Counter()
    other_break = Counter()
    feat = Counter()
    zero_chunk: list[dict] = []
    infobox_names_seen = Counter()
    all_infobox_like = Counter()
    total = 0
    chunk_by_page: Counter = Counter()

    # 顺带把每页 wikitext 里出现的所有"图鉴/数据"类模板名记下来，找第 10 种
    LIKE_RX = re.compile(r"^\s*{{\s*([^{}|\n]+?)\s*(?:\|[^}]*)?$", re.M)

    for shard in sorted(args.source.glob("*.jsonl.gz")):
        with gzip.open(shard, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                title = rec["title"]
                wt = rec["wikitext"]
                total += 1

                found = cleantext.top_level_infobox(wt, infobox_names)
                if found:
                    kind = TEMPLATE_KINDS[cleantext.infobox_template_name(found[0])][0]
                elif STAGE_RE.match(title):
                    kind = "stage"
                else:
                    kind = "other"
                    other_break[classify(title, wt)] += 1
                kinds[kind] += 1

                for k, rx in FEATURES.items():
                    if rx.search(wt):
                        feat[k] += 1

                # infobox 候选：顶层模板里带"图鉴"或字段很多的
                for m in LIKE_RX.finditer(wt[:4000]):
                    n = m.group(1).strip()
                    if "图鉴" in n or "信息" in n or "数据" in n or "档案" in n:
                        all_infobox_like[n] += 1

                if kind == "other" and len(wt) > 3000:
                    body = cleantext.to_plain(found[1] if found else wt)
                    if len(body) < 200:
                        zero_chunk.append({"title": title, "len": len(wt),
                                           "body": len(body)})

    result = {
        "pages": total,
        "kinds": dict(kinds),
        "other_breakdown": dict(other_break.most_common()),
        "features": dict(feat.most_common()),
        "infobox_like_templates": dict(all_infobox_like.most_common(40)),
        "other_long_but_empty": zero_chunk[:60],
        "other_long_but_empty_n": len(zero_chunk),
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"页数 {total}  类型 {dict(kinds)}")
    print("\nother 拆解：")
    for k, v in other_break.most_common():
        print(f"  {k:<14} {v}")
    print("\n语法特征（出现页数）：")
    for k, v in feat.most_common():
        print(f"  {k:<14} {v}")
    print("\ninfobox 候选模板名 top：")
    for k, v in all_infobox_like.most_common(25):
        print(f"  {k:<28} {v}")
    print(f"\nother 里 wikitext>3000 但清洗后<200 字的页：{len(zero_chunk)}")
    for r in zero_chunk[:15]:
        print("   ", r)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
