"""一次性升级旧 NIKKE 快照与订阅：python -m app.services.promote_nikke，可重复执行。"""

import asyncio
import json

from sqlalchemy import select, update

from app.custom_presets import CUSTOM_PRESETS, promote_subscriptions
from app.db import SessionLocal
from app.models import BotTarget, CustomFeedSource, CustomSourceItem, RawContent
from app.services.ingest import ingest_items


async def promote_nikke(session):
    imported = skipped = subscriptions = 0
    for preset in CUSTOM_PRESETS.values():
        if preset.get("community") != "nikke":
            continue
        source_id = await session.scalar(select(CustomFeedSource.id).where(
            CustomFeedSource.platform == preset["platform"], CustomFeedSource.kind == preset["kind"],
            CustomFeedSource.target_value == preset["target_value"]))
        if source_id is None:
            continue
        rows = list(await session.scalars(select(CustomSourceItem).where(CustomSourceItem.source_id == source_id)))
        existing = set(await session.scalars(select(RawContent.external_id).where(
            RawContent.platform == preset["platform"], RawContent.external_id.in_([row.external_id for row in rows]))))
        missing = [row for row in rows if row.external_id not in existing]
        skipped += len(rows) - len(missing)
        stats = await ingest_items(session, "custom_source_archive", [{
            "archive_source": "custom_source_items", "custom_source_id": source_id,
            "custom_source_item_id": row.id, "normalized": row.normalized,
            "_matched_keywords": ["NIKKE"],
        } for row in missing], community="nikke")
        if stats.parse_failed or stats.errors:
            raise RuntimeError(f"NIKKE 历史导入失败：{stats.errors}")
        imported += stats.items_new
        # 历史导入沿用首次采集时间，避免迁移记录被当作刚发生的新消息推送。
        for row in missing:
            await session.execute(update(RawContent).where(RawContent.platform == preset["platform"],
                RawContent.external_id == row.external_id, RawContent.community == "nikke")
                .values(collected_at=row.collected_at, last_seen_at=row.collected_at))
        await session.commit()
    for target in await session.scalars(select(BotTarget)):
        config = promote_subscriptions(target.config)
        if config != target.config:
            target.config = config
            subscriptions += 1
    await session.commit()
    return {"imported": imported, "already_present": skipped, "subscriptions_migrated": subscriptions}


async def main():
    async with SessionLocal() as session:
        print(json.dumps(await promote_nikke(session), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
