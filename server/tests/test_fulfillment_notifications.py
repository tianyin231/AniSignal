"""履约状态修订与即时邮件；所有 SMTP 均使用测试替身。"""

from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import (
    NotificationDelivery, Product, ProductFulfillmentRevision, ProductSignal, ProductSignalEvidence, User,
)
from app.services import instant_notifications as instant
from app.services.fulfillment import build_fulfillment_snapshot
from app.services.fulfillment_revisions import record_product_fulfillment_change
from tests.test_notifications import _content, _user_with_keyword


@pytest.fixture(autouse=True)
def sender(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(instant, "send_email", mock)
    return mock


async def _product(session):
    user = await _user_with_keyword(session)
    user.instant_notification_enabled = True
    user.instant_notification_cursor = 0
    user.instant_fulfillment_cursor = 0
    product = Product(slug="keyboard", name="爱弥斯联名键盘", watch_terms=["爱弥斯"], community="wuwa")
    session.add(product)
    await session.commit()
    return user, product


async def _receipt(session, product, index=1):
    raw = await _content(session, f"爱弥斯键盘自购开箱 {index}")
    signal = ProductSignal(
        product_id=product.id, community=product.community, signal_type="consumer_received",
        actor_type="user", review_status="verified", locked=True, active=True,
        source_method="rule", summary=f"自购到货 {index}", occurred_at=raw.published_at,
    )
    session.add(signal)
    await session.flush()
    session.add(ProductSignalEvidence(signal_id=signal.id, raw_content_id=raw.id,
                                      claim_quote="已收到自购商品", acquisition_type="self_purchase",
                                      independent_source_key=f"user-{index}"))
    await session.commit()
    return signal, raw


async def _record(session, product, before, reason="内部审核意见不得发给用户"):
    after = await build_fulfillment_snapshot(session, product.id)
    revision = await record_product_fulfillment_change(session, product.id, before, after, reason)
    await session.commit()
    return revision, after


async def test_only_verified_state_changes_create_revision(session):
    _, product = await _product(session)
    before = await build_fulfillment_snapshot(session, product.id)
    candidates = deepcopy(before)
    candidates["quality"].update(state="candidate_attention", candidate_count=2, ai_preview="AI 预测风险")
    candidates["candidate_total"] = 2
    assert await record_product_fulfillment_change(session, product.id, before, candidates, "新增候选") is None
    await _receipt(session, product)
    revision, after = await _record(session, product, before)
    assert revision is not None and revision.snapshot["consumer"]["verified_count"] == 1
    assert isinstance(revision.snapshot["consumer"]["updated_at"], str)
    assert await record_product_fulfillment_change(session, product.id, before, after, "重试") is None
    after["consumer"]["updated_at"] = datetime(2030, 1, 1)
    assert await record_product_fulfillment_change(session, product.id, revision.snapshot, after, "仅更新时间") is None


async def test_verified_receipt_and_revocation_send_once_with_current_evidence(session, sender):
    _, product = await _product(session)
    before = await build_fulfillment_snapshot(session, product.id)
    signal, raw = await _receipt(session, product)
    _, verified = await _record(session, product, before)
    assert await instant.run_instant_notifications(session) == {"created": 1, "sent": 1, "failed": 0, "skipped": 0}
    first = await session.scalar(select(NotificationDelivery))
    assert first.content_ids == [raw.id] and "首位普通用户到货" in first.body
    assert "内部审核意见" not in first.body
    assert (await instant.run_instant_notifications(session))["sent"] == 0
    signal.review_status = "rejected"
    await _record(session, product, verified)
    assert (await instant.run_instant_notifications(session))["sent"] == 1
    last = await session.scalar(select(NotificationDelivery).order_by(NotificationDelivery.id.desc()))
    assert "履约更正" in last.subject and "暂无已核验消费者到货" in last.body
    assert last.content_ids == [] and "自购开箱" not in last.body
    assert sender.await_count == 2


async def test_obsolete_upgrade_is_skipped_and_hidden_evidence_is_not_in_correction(session, sender):
    _, product = await _product(session)
    before = await build_fulfillment_snapshot(session, product.id)
    _, raw = await _receipt(session, product)
    _, verified = await _record(session, product, before)
    assert await instant.prepare_instant_notifications(session) == 1
    first = await session.scalar(select(NotificationDelivery))
    raw.hidden = True
    await _record(session, product, verified)
    assert await instant.send_instant_notification(session, first.id) == {"status": "skipped"}
    sender.assert_not_awaited()
    assert (await instant.run_instant_notifications(session))["sent"] == 1
    assert "自购开箱" not in sender.await_args.args[2]
    assert "暂无已核验消费者到货" in sender.await_args.args[2]


@pytest.mark.parametrize("change", ["mail_disabled", "instant_disabled", "keyword", "community", "excluded", "moved", "hidden_without_revision"])
async def test_fulfillment_send_rechecks_current_settings_and_state(session, sender, change):
    from app.models import UserKeyword

    user, product = await _product(session)
    before = await build_fulfillment_snapshot(session, product.id)
    _, raw = await _receipt(session, product)
    await _record(session, product, before)
    assert await instant.prepare_instant_notifications(session) == 1
    row = await session.scalar(select(NotificationDelivery))
    if change == "mail_disabled":
        user.notification_enabled = False
    elif change == "instant_disabled":
        user.instant_notification_enabled = False
    elif change == "keyword":
        (await session.scalar(select(UserKeyword).where(UserKeyword.user_id == user.id))).enabled = False
    elif change == "community":
        user.feed_preferences = {"communities": ["blhx"]}
    elif change == "excluded":
        user.feed_preferences = {"exclude_keywords": ["爱弥斯"]}
    elif change == "moved":
        product.community = "blhx"
    else:
        raw.hidden = True
    await session.commit()
    assert await instant.send_instant_notification(session, row.id) == {"status": "skipped"}
    sender.assert_not_awaited()


async def test_turning_on_resets_both_cursors_and_deployment_baseline_skips_history(client, session):
    from app.services.auth import create_session

    user, product = await _product(session)
    before = await build_fulfillment_snapshot(session, product.id)
    signal, _ = await _receipt(session, product)
    revision, verified = await _record(session, product, before)
    # 已开启事件邮件的存量用户在升级后从当前履约位置开始。
    user.instant_fulfillment_cursor = None
    await session.commit()
    assert await instant.prepare_instant_notifications(session) == 0
    assert user.instant_fulfillment_cursor == revision.id
    token = await create_session(session, user)
    client.cookies.set("anisignal_session", token)
    assert (await client.put("/api/account/notifications", json={"enabled": False})).status_code == 200
    signal.review_status = "rejected"
    revision, _ = await _record(session, product, verified)
    assert (await client.put("/api/account/notifications", json={"enabled": True})).status_code == 200
    await session.refresh(user)
    assert user.instant_fulfillment_cursor == revision.id
    assert await instant.prepare_instant_notifications(session) == 0


async def test_multiple_revisions_for_one_product_coalesce(session, sender):
    _, product = await _product(session)
    before = await build_fulfillment_snapshot(session, product.id)
    for i in range(1, 4):
        await _receipt(session, product, i)
        _, before = await _record(session, product, before)
    result = await instant.run_instant_notifications(session)
    assert result["created"] == result["sent"] == 1
    assert "多名独立用户到货" in sender.await_args.args[2]
    assert await session.scalar(select(ProductFulfillmentRevision.id).order_by(ProductFulfillmentRevision.id.desc())) == 3


async def test_manual_review_records_change_and_reaches_existing_instant_worker(session, sender):
    from app.services.fulfillment import review_product_signal

    _, product = await _product(session)
    signal, _ = await _receipt(session, product)
    signal.review_status = "candidate"
    signal.locked = False
    await session.commit()
    for status in ("verified", "rejected"):
        await review_product_signal(
            session, product.id, signal.id, review_status=status, actor_type="user",
            acquisition_type="self_purchase", summary=None, claim_quote=None, note="内部审核意见",
        )
        await session.commit()
        assert (await instant.run_instant_notifications(session))["sent"] == 1
    assert sender.await_count == 2
    assert "履约更正" in sender.await_args.args[1]
    assert "内部审核意见" not in sender.await_args.args[2]
