"""命令行导入预设采集目标：`python -m app.seeds [--all | --communities wuwa,ai]`。"""

import argparse
import asyncio
import json

from app.db import SessionLocal
from app.seeds import import_preset_targets, load_preset_packs


async def _run(communities: list[str] | None) -> None:
    async with SessionLocal() as session:
        result = await import_preset_targets(session, communities)
    print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="按分区幂等导入预设采集目标（已存在的目标不会覆盖）")
    parser.add_argument(
        "--all",
        action="store_true",
        help="导入全部预设分区的采集目标（默认行为）",
    )
    parser.add_argument(
        "--communities",
        default="",
        help=f"逗号分隔的分区标识（留空导入全部：{','.join(load_preset_packs())}）",
    )
    args = parser.parse_args()
    selected = [item.strip() for item in args.communities.split(",") if item.strip()] or None
    asyncio.run(_run(selected))


if __name__ == "__main__":
    main()
