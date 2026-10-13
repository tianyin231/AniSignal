"""首批 X AI 账号；与其他来源共用管理员采集目标配置。"""

from alembic import op
import sqlalchemy as sa

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

ACCOUNTS = (
    ("openai", "X · OpenAI"),
    ("deepseek_ai", "X · DeepSeek"),
    ("bcherny", "X · Boris Cherny"),
    ("thsottiaux", "X · Tibo"),
    ("testingcatalog", "X · TestingCatalog"),
)


def upgrade():
    connection = op.get_bind()
    for account, name in ACCOUNTS:
        connection.execute(sa.text(
            "INSERT INTO collect_targets "
            "(platform,target_type,target_value,display_name,interval_minutes,enabled,"
            "co_terms,min_views,tier,community,consecutive_failures,next_run_at,created_at,updated_at) "
            "SELECT 'x','account_dynamics',:account,:name,60,1,'[]',0,'general','ai',0,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP WHERE NOT EXISTS "
            "(SELECT 1 FROM collect_targets WHERE platform='x' AND target_type='account_dynamics' "
            "AND target_value=:account)"
        ), {"account": account, "name": name})


def downgrade():
    connection = op.get_bind()
    for account, _ in ACCOUNTS:
        connection.execute(sa.text("DELETE FROM collect_targets WHERE platform='x' "
            "AND target_type='account_dynamics' AND target_value=:account"), {"account": account})
