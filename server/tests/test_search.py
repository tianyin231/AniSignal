"""站内全文检索测试：ngram 布尔词项拆解、MySQL SQL 编译、正文+跨语种译文检索与安全转义。"""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql

from app.clock import utcnow
from app.models import ContentTranslation, ContentVersion, Event, Evidence, RawContent
from app.services.search import (
    content_search_condition,
    event_search_condition,
    parse_search_query,
)


def test_parse_search_query_handles_cjk_punctuation_stopwords_and_operators():
    p1 = parse_search_query("卡提希娅 前瞻直播")
    assert p1.fts_segments == ("卡提希娅", "前瞻直播")
    assert p1.boolean_expr == '+"卡提希娅" +"前瞻直播"'
    assert p1.like_terms == ()

    # 带点号版本号：鸣潮走 ngram FTS，2.4 由 LIKE 精确复核，避免 InnoDB 按 "." 切出 1 字符空结果
    p2 = parse_search_query("鸣潮 2.4")
    assert p2.fts_segments == ("鸣潮",)
    assert p2.boolean_expr == '+"鸣潮"'
    assert p2.like_terms == ("2.4",)

    # 单字与纯停用词：不生成空洞 FTS 表达式，自动回退 LIKE
    p3 = parse_search_query("新")
    assert p3.boolean_expr == ""
    assert p3.like_terms == ("新",)

    p4 = parse_search_query("in on")
    assert p4.boolean_expr == ""
    assert p4.like_terms == ("in", "on")

    # 英文词内含 2-char 停用词 bigram（discord 含 is/or）：同时走 FTS 缩表 + LIKE 复核
    p5 = parse_search_query("Discord")
    assert p5.fts_segments == ("Discord",)
    assert p5.like_terms == ("Discord",)

    # 布尔运算符注入安全剥离
    p6 = parse_search_query('+鸣潮 -测试* "卡池" @(1)')
    assert '+"鸣潮"' in p6.boolean_expr
    assert '+"测试"' in p6.boolean_expr
    assert '+"卡池"' in p6.boolean_expr
    assert "@" not in p6.boolean_expr

    # 纯通配符不会崩溃
    p7 = parse_search_query("% _ *")
    assert p7.boolean_expr == ""
    assert p7.like_terms == ("%", "_", "*")


def test_mysql_fts_sql_compilation_uses_union_and_distinct_params():
    c_cond = content_search_condition("鸣潮 2.4", use_mysql_fts=True)
    stmt = (
        select(RawContent.id)
        .join(ContentVersion, ContentVersion.id == RawContent.latest_version_id)
        .where(c_cond)
    )
    compiled = str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "MATCH(cv_ft.title, cv_ft.text) AGAINST(" in compiled
    assert "MATCH(ct_ft.text) AGAINST(" in compiled
    assert "UNION" in compiled
    assert '+"鸣潮"' in compiled
    assert "2.4" in compiled

    e_cond = event_search_condition("前瞻直播", use_mysql_fts=True)
    e_stmt = select(Event.id).where(e_cond)
    e_compiled = str(e_stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "MATCH(e_ft.title, e_ft.summary) AGAINST(" in e_compiled
    assert "MATCH(cv_ft.title, cv_ft.text) AGAINST(" in e_compiled
    assert "MATCH(ct_ft.text) AGAINST(" in e_compiled
    assert '+"前瞻直播"' in e_compiled


@pytest.mark.asyncio
async def test_contents_search_matches_body_and_chinese_translation(session, client):
    now = utcnow()
    # 1. 英文原文 + 中文译文（标题不含中文）
    en_raw = RawContent(
        platform="youtube",
        content_type="video",
        external_id="YT_FTS_1",
        community="wuwa",
        published_at=now - timedelta(hours=1),
        content_hash="fts_en_1",
        raw_json={},
    )
    # 2. 中文内容：关键词只出现在正文中
    cn_raw = RawContent(
        platform="bilibili",
        content_type="dynamic",
        external_id="BL_FTS_2",
        community="wuwa",
        published_at=now - timedelta(hours=2),
        content_hash="fts_cn_2",
        raw_json={},
    )
    # 3. 干扰项
    other_raw = RawContent(
        platform="bilibili",
        content_type="dynamic",
        external_id="BL_FTS_3",
        community="wuwa",
        published_at=now - timedelta(hours=3),
        content_hash="fts_cn_3",
        raw_json={},
    )
    session.add_all([en_raw, cn_raw, other_raw])
    await session.flush()
    session.add_all([
        ContentVersion(
            raw_content_id=en_raw.id,
            version=1,
            title="Wuthering Waves 2.4 Special Broadcast",
            text="Join the official livestream for Version 2.4 preview.",
        ),
        ContentTranslation(
            raw_content_id=en_raw.id,
            lang="zh-CN",
            text="鸣潮 2.4 版本特别前瞻直播公告，揭晓新共鸣者卡提希娅。",
            model="test",
        ),
        ContentVersion(
            raw_content_id=cn_raw.id,
            version=1,
            title="开发组访谈纪要",
            text="本次更新将实装新共鸣者卡提希娅与声骸系统优化。",
        ),
        ContentVersion(
            raw_content_id=other_raw.id,
            version=1,
            title="日常维护公告",
            text="例行服务器重启维护。",
        ),
    ])
    await session.commit()

    # 搜中文词应同时命中「正文」和「外文机翻」
    r1 = await client.get("/api/contents", params={"community": "wuwa", "q": "卡提希娅"})
    assert r1.status_code == 200
    assert {item["id"] for item in r1.json()["items"]} == {en_raw.id, cn_raw.id}

    # 多词跨字段检索（版本号 + 译文词）
    r2 = await client.get("/api/contents", params={"community": "wuwa", "q": "2.4 前瞻直播"})
    assert r2.status_code == 200
    assert [item["id"] for item in r2.json()["items"]] == [en_raw.id]

    # SQL 通配符安全转义，不能把所有行都匹配出来
    r3 = await client.get("/api/contents", params={"community": "wuwa", "q": "%"})
    assert r3.status_code == 200
    assert r3.json()["total"] == 0


@pytest.mark.asyncio
async def test_events_search_matches_summary_and_translated_evidence(session, client):
    now = utcnow()
    raw = RawContent(
        platform="kuro_global",
        content_type="notice",
        external_id="KG_EV_1",
        community="wuwa",
        published_at=now - timedelta(hours=1),
        content_hash="fts_ev_1",
        raw_json={},
    )
    other_raw = RawContent(
        platform="kuro_cn",
        content_type="notice",
        external_id="KC_EV_2",
        community="wuwa",
        published_at=now - timedelta(hours=2),
        content_hash="fts_ev_2",
        raw_json={},
    )
    session.add_all([raw, other_raw])
    await session.flush()
    session.add_all([
        ContentVersion(
            raw_content_id=raw.id,
            version=1,
            title="Global Compensation Notice",
            text="Compensation details for server issue.",
        ),
        ContentTranslation(
            raw_content_id=raw.id,
            lang="zh-CN",
            text="国际服停服补偿说明：发放星声与调频仪。",
            model="test",
        ),
        ContentVersion(
            raw_content_id=other_raw.id,
            version=1,
            title="周边预售开启",
            text="新款立牌开启预约。",
        ),
    ])
    ev1 = Event(
        title="Global Server Issue",
        summary="海外节点波动后官方发布补偿安排。",
        category="version_update",
        community="wuwa",
        identity="official",
        is_active=True,
    )
    ev2 = Event(
        title="新款立牌预售",
        summary="官方旗舰店开放新款亚克力立牌预售。",
        category="presale",
        community="wuwa",
        identity="official",
        is_active=True,
    )
    session.add_all([ev1, ev2])
    await session.flush()
    session.add_all([
        Evidence(event_id=ev1.id, raw_content_id=raw.id, role="support"),
        Evidence(event_id=ev2.id, raw_content_id=other_raw.id, role="support"),
    ])
    await session.commit()

    # 命中事件 summary
    r1 = await client.get("/api/events", params={"community": "wuwa", "q": "海外节点波动"})
    assert r1.status_code == 200
    assert [item["id"] for item in r1.json()["items"]] == [ev1.id]

    # 命中外文证据的中文译文
    r2 = await client.get("/api/events", params={"community": "wuwa", "q": "调频仪"})
    assert r2.status_code == 200
    assert [item["id"] for item in r2.json()["items"]] == [ev1.id]

    # 命中另一个事件的正文
    r3 = await client.get("/api/events", params={"community": "wuwa", "q": "亚克力立牌"})
    assert r3.status_code == 200
    assert [item["id"] for item in r3.json()["items"]] == [ev2.id]


def test_migration_0060_skips_cleanly_when_ngram_plugin_absent(monkeypatch):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    mig_path = Path(__file__).parents[1] / "alembic/versions/0060_ngram_fulltext_search.py"
    spec = importlib.util.spec_from_file_location("mig_0060_compat", mig_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    executed: list[str] = []

    class _FakeScalarResult:
        def __init__(self, value):
            self._value = value

        def scalar(self):
            return self._value

    class _FakeBind:
        def __init__(self, dialect_name: str, has_ngram: bool):
            self.dialect = SimpleNamespace(name=dialect_name)
            self._has_ngram = has_ngram

        def execute(self, _stmt):
            return _FakeScalarResult(1 if self._has_ngram else None)

    monkeypatch.setattr(mod.op, "get_context", lambda: SimpleNamespace(as_sql=False))
    monkeypatch.setattr(mod.op, "execute", lambda stmt: executed.append(str(stmt)))
    monkeypatch.setattr(mod, "_inspector", lambda _bind: SimpleNamespace(get_indexes=lambda _t: []))

    # MariaDB / 无 ngram 插件：安全跳过，不执行任何 ALTER TABLE
    monkeypatch.setattr(mod.op, "get_bind", lambda: _FakeBind("mariadb", has_ngram=False))
    mod.upgrade()
    assert executed == []

    # MySQL 8.4（激活 ngram 插件）：正常创建全部 3 个 FULLTEXT 索引
    monkeypatch.setattr(mod.op, "get_bind", lambda: _FakeBind("mysql", has_ngram=True))
    mod.upgrade()
    assert len(executed) == 3
    assert all("WITH PARSER ngram" in sql for sql in executed)


@pytest.mark.asyncio
async def test_supports_mysql_fts_probing_caching_and_fallback(session):
    from types import SimpleNamespace
    from app.services.search import reset_fts_capability_cache, supports_mysql_fts

    reset_fts_capability_cache()

    # 1. SQLite 直接返回 False（不发查询）
    assert await supports_mysql_fts(session) is False

    class _FakeEngine:
        def __init__(self, dialect_name: str):
            self.dialect = SimpleNamespace(name=dialect_name)

    class _FakeSession:
        def __init__(self, engine, count_or_exc):
            self._engine = engine
            self._count_or_exc = count_or_exc
            self.calls = 0

        def get_bind(self):
            return self._engine

        async def scalar(self, _stmt):
            self.calls += 1
            if isinstance(self._count_or_exc, Exception):
                raise self._count_or_exc
            return self._count_or_exc

    # 2. MySQL 8.4 存在全部 3 个索引 -> 返回 True 且仅查询一次（命中缓存）
    mysql_engine = _FakeEngine("mysql")
    s_ok = _FakeSession(mysql_engine, 3)
    assert await supports_mysql_fts(s_ok) is True
    assert await supports_mysql_fts(s_ok) is True
    assert s_ok.calls == 1

    # 3. MariaDB / 无索引 -> 返回 False 且仅查询一次（命中缓存）
    mariadb_engine = _FakeEngine("mariadb")
    s_no_idx = _FakeSession(mariadb_engine, 0)
    assert await supports_mysql_fts(s_no_idx) is False
    assert await supports_mysql_fts(s_no_idx) is False
    assert s_no_idx.calls == 1

    # 4. 瞬时异常 -> 本次安全回退 False，不污染缓存；恢复后可重新探测到 True
    flaky_engine = _FakeEngine("mysql")
    s_flaky = _FakeSession(flaky_engine, RuntimeError("transient"))
    assert await supports_mysql_fts(s_flaky) is False
    s_flaky._count_or_exc = 3
    assert await supports_mysql_fts(s_flaky) is True
    assert s_flaky.calls == 2
    reset_fts_capability_cache()
