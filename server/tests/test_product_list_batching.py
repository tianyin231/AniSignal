"""商品列表批量视图：查询次数有界，输出与单商品版一致。"""

from sqlalchemy import event, select

from app.clock import utcnow
from app.models import CollectTarget, Product, ProductSignal, ProductSignalEvidence, RawContent, Source, ContentVersion
from app.services.views import product_to_out, products_to_out


async def _seed_product(session, *, name: str, slug: str, with_signal: bool) -> Product:
    target = CollectTarget(
        platform="bilibili", target_type="keyword_search", target_value=f"鸣潮 {name} 手办",
        community="wuwa", tier="core", enabled=True, interval_minutes=30,
    )
    session.add(target)
    await session.flush()
    product = Product(
        slug=slug, name=name, community="wuwa", watch_terms=[name],
        monitor_status="watching", monitor_target_id=target.id,
        price_note="¥699 起", presale_at="2026-10-01",
    )
    session.add(product)
    await session.flush()
    if with_signal:
        source = Source(platform="bilibili", external_id=f"u-{slug}", name="测试账号", identity="community")
        session.add(source)
        await session.flush()
        raw = RawContent(platform="bilibili", content_type="video", external_id=f"BV-{slug}",
                         community="wuwa", source_id=source.id, url=f"https://www.bilibili.com/{slug}",
                         collected_at=utcnow())
        session.add(raw)
        await session.flush()
        session.add(ContentVersion(raw_content_id=raw.id, version=1, title=f"{name} 开箱",
                                   text="今天到货了", parsed_at=utcnow()))
        signal = ProductSignal(product_id=product.id, signal_type="consumer_received", actor_type="user",
                               review_status="verified", summary=f"{name} 开箱到货", occurred_at=utcnow(),
                               community="wuwa")
        session.add(signal)
        await session.flush()
        session.add(ProductSignalEvidence(signal_id=signal.id, raw_content_id=raw.id,
                                          independent_source_key=f"k-{slug}", acquisition_type="self_purchase"))
    return product


async def test_products_to_out_matches_single_product_output(session):
    await _seed_product(session, name="长离手办", slug="p1", with_signal=True)
    await _seed_product(session, name="卡提希娅手办", slug="p2", with_signal=True)
    await _seed_product(session, name="莫宁手办", slug="p3", with_signal=False)
    await session.commit()

    products = list(await session.scalars(select(Product).order_by(Product.id)))
    batched = await products_to_out(session, products, include_pending=True)
    singles = [await product_to_out(session, p, include_pending=True) for p in products]
    assert [item.model_dump(mode="json") for item in batched] == [
        item.model_dump(mode="json") for item in singles
    ]
    assert batched[0].brief.unboxing_accounts == 1
    assert batched[2].brief.unboxing_accounts == 0


async def test_list_queries_stay_bounded_when_products_grow(session_factory, engine):

    async with session_factory() as setup:
        for index in range(6):
            await _seed_product(setup, name=f"商品{index}", slug=f"b{index}", with_signal=True)
        await setup.commit()

    counts: list[int] = []

    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            counts.append(1)

    async with session_factory() as probe:
        event.listen(engine.sync_engine, "before_cursor_execute", _count)
        try:
            products = list(await probe.scalars(select(Product).order_by(Product.id)))
            await products_to_out(probe, products)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _count)
    # 6 个商品：批量视图应为常数级查询（商品+信号+hints+证据+标题+监控目标 ≈ 6），
    # 逐商品 N+1 会是 6×4 以上。
    assert sum(counts) < 14
