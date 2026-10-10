"""AI 资讯候选摘要与首批 B 站关键词；播放量不作为快讯门槛。"""

from alembic import op
import sqlalchemy as sa

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

KEYWORDS = ("AI 快讯", "Codex", "Claude", "Gemini", "DeepSeek")


def upgrade():
    op.add_column("content_reviews", sa.Column("news", sa.JSON(), nullable=True))
    connection = op.get_bind()
    for keyword in KEYWORDS:
        connection.execute(sa.text(
            "INSERT INTO collect_targets "
            "(platform,target_type,target_value,display_name,interval_minutes,enabled,"
            "co_terms,min_views,tier,community,consecutive_failures,created_at,updated_at) "
            "SELECT 'bilibili','keyword_search',:keyword,:name,30,1,'[]',0,'general','ai',0,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP WHERE NOT EXISTS "
            "(SELECT 1 FROM collect_targets WHERE platform='bilibili' "
            "AND target_type='keyword_search' AND target_value=:keyword)"
        ), {"keyword": keyword, "name": f"AI 快讯 · {keyword}"})


def downgrade():
    connection = op.get_bind()
    for keyword in KEYWORDS:
        connection.execute(sa.text(
            "DELETE FROM collect_targets WHERE community='ai' AND platform='bilibili' "
            "AND target_type='keyword_search' AND target_value=:keyword"
        ), {"keyword": keyword})
    op.drop_column("content_reviews", "news")
