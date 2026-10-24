"""流式保留旧引擎审计数据；迁移重跑使用同一键，不重复记账。"""

import hashlib
import json

from app.clock import utcnow
from app.models import NewsArchive


async def import_history(session, path, records):
    dialect = session.bind.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert
    else:
        raise ValueError("统一资讯只支持主项目的 MySQL 或本地 SQLite")
    batch = []
    async def flush():
        if not batch:
            return
        statement = insert(NewsArchive).values(batch)
        if dialect == "sqlite":
            statement = statement.on_conflict_do_nothing(index_elements=["kind", "key"])
        else:
            statement = statement.on_duplicate_key_update(key=statement.inserted.key)
        await session.execute(statement)
        await session.commit()
        batch.clear()
    for item in records(path):
        name, row = item["table"], item["row"]
        # 文章已以 article 映射完整保存；其他原始记录保留独立审计键。
        if name == "articles":
            continue
        key = row.get("id")
        if key is None:
            key = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        batch.append({"kind": "legacy_" + name, "key": str(key), "payload": row,
            "raw_content_id": None, "event_id": None, "created_at": utcnow()})
        if len(batch) >= 100:
            await flush()
    await flush()
