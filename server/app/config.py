"""Application configuration, driven by environment variables (prefix ANISIGNAL_)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANISIGNAL_", env_file=".env", extra="ignore"
    )

    database_url: str = "mysql+asyncmy://root:anisignal@localhost:3306/anisignal"
    redis_url: str = "redis://localhost:6379/0"

    # 单管理员 Token；生产必须通过环境变量覆盖
    admin_token: str = "change-me"

    # 本地测试模式（python -m app.local 自动开启）：
    # SQLite + 进程内任务执行，不需要 MySQL / Redis / Docker
    local_mode: bool = False

    # bilibili CLI
    bili_bin: str = "bili"
    bili_timeout_seconds: float = 120.0
    bili_max_retries: int = 2

    # 鸣潮官方账号等账号 UID 走配置，禁止硬编码（见项目 skill 合规红线）
    wuthering_waves_bili_mid: str = "1955897084"

    # 米游社默认游戏 ID（2 = 原神），管理员看板可按目标覆盖
    miyoushe_default_gids: int = 2

    # 调度器
    scheduler_poll_seconds: float = 30.0
    scheduler_jitter_ratio: float = 0.1
    scheduler_failure_backoff_max: int = 5

    # 平台礼貌间隔（秒）：同平台两次采集的最小间隔
    platform_min_interval_seconds: float = 5.0

    # 每平台单次采集条数上限
    collect_limit: int = 20

    # HTTP collector 公共参数
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
