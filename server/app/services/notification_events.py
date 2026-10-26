"""跨通道通知输入；目标授权和投递状态由通道适配器负责。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class UserNotice:
    user_id: int
    scope: str
    dedupe_key: str
    subject: str
    body: str
    content_ids: list[int]


def custom_feed_notice(feed, latest, new_count, day) -> UserNotice:
    # 先生成一次确定性通知，再交给邮件适配器；不为各通道重复调用摘要模型。
    # AstrBot 后续需先验证 user_id -> BotTarget 的授权绑定，再复用此事件；
    # 个人私有监控不能仅凭用户提交的群号或 target_id 推入机器人会话。
    lines = [f"- {item.title or item.external_id}" + (f"\n  {item.url}" if item.url else "") for item in latest]
    subject = f"定制监控「{feed.name}」有 {new_count} 条新内容"
    body = (f"你监控的「{feed.name}」新增 {new_count} 条内容，以下为最近 {len(latest)} 条：\n"
            + "\n".join(lines) + "\n\n每个监控每天最多发送一封摘要（北京时间）。可在账号中心关闭此监控推送或关闭邮件通知。")
    return UserNotice(feed.user_id, f"custom-feed:{feed.id}", f"custom-push:{feed.id}:{day.isoformat()}",
                      subject[:255], body, [item.id for item in latest])
