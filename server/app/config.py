"""Application configuration, driven by environment variables (prefix ANISIGNAL_)."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 写在公开仓库里的默认令牌：见 SEC-001。留着它只是为了本地开发的开箱即用，
# 任何非 local_mode 的启动都不允许带着它跑起来。
_DEFAULT_ADMIN_TOKENS = frozenset({"", "change-me"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANISIGNAL_", env_file=".env", extra="ignore"
    )

    database_url: str = "mysql+asyncmy://root:anisignal@localhost:3306/anisignal"
    redis_url: str = "redis://localhost:6379/0"

    # 单管理员 Token；生产必须通过环境变量覆盖
    admin_token: str = "change-me"

    # 对外站点基址（生成 RSS 等外部引用链接用；留空则回落到请求基址）
    public_base_url: str = ""

    # AI 密钥池 env 兜底（逗号分隔；DB site_settings 的 api_keys 优先）
    ai_api_keys: str = ""

    # YouTube Data API v3 key（配置后 YouTube 目标改走官方接口，RSS 作为失败回退）
    youtube_api_key: str = ""

    # 允许的跨域来源（逗号分隔；web 部署地址变化时在此追加）
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _reject_default_admin_token(self) -> "Settings":
        """SEC-001：非 local_mode 不允许带着默认管理员令牌启动。

        原先 `admin_token` 默认 `change-me`，compose 再用 `${VAR:-change-me}` 兜一次底，
        `.env.example` 甚至直接把它当建议值——运维一旦漏配环境变量，`/admin/*` 全部接口
        （用户管理、规则引擎、AI 密钥、bot 通道）就对一个写在公开仓库里的字符串开放，
        且没有任何启动期提示。这里 fail-fast：让进程起不来，而不是静默地裸奔。
        `local_mode`（`python -m app.local`）保持开箱即用，不受此限。
        """
        if not self.local_mode and self.admin_token.strip() in _DEFAULT_ADMIN_TOKENS:
            raise ValueError(
                "ANISIGNAL_ADMIN_TOKEN 仍是默认值，拒绝启动。"
                "请在环境变量或 .env 里设置一个足够长的随机令牌"
                "（如 python -c \"import secrets; print(secrets.token_urlsafe(32))\"）；"
                "本地开发可用 ANISIGNAL_LOCAL_MODE=true 跳过本检查。"
            )
        return self

    # 本地测试模式（python -m app.local 自动开启）：
    # SQLite + 进程内任务执行，不需要 MySQL / Redis / Docker
    local_mode: bool = False

    # bilibili CLI
    bili_bin: str = "bili"
    bili_timeout_seconds: float = 120.0
    bili_max_retries: int = 2

    # 鸣潮官方账号等账号 UID 走配置，禁止硬编码（见项目 skill 合规红线）
    wuthering_waves_bili_mid: str = "1955897084"
    # 碧蓝航线官方 B 站账号（UID 2026-09-02 经 bili CLI 核验，认证"碧蓝航线官方账号"）
    azur_lane_bili_mid: str = "233114659"
    # 崩坏星穹铁道官方 B 站账号（UID 2026-09-05 经 bili CLI 核验，认证"崩坏：星穹铁道官方账号"）
    star_rail_bili_mid: str = "1340190821"
    # 明日方舟官方 B 站账号（UID 2026-09-06 经 bili CLI 核验，认证"明日方舟官方账号"）
    arknights_bili_mid: str = "161775300"

    # YouTube 官方频道白名单："频道ID:名称" 逗号分隔（分区归属由采集目标的 community 决定）
    official_youtube_channels: str = (
        "UC0Bi5KMcECRVYis5Gb_ZYZQ:Wuthering Waves 官方频道,"
        "UCPeJaqP6WgxUVDcpYkXZKMQ:Azur Lane Official,"
        "UC2PeMPA8PAOp-bynLoCeMLA:Honkai Star Rail 官方频道,"
        "UCR0J2NYGuC8epsa1O4DMmXQ:Arknights Official"
    )

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

    # 邮件发送（通用 SMTP；未配置 host 时验证码只落库，管理端可查）
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "AniSignal <noreply@anisignal.local>"
    smtp_starttls: bool = True
    smtp_tls_mode: str = ""

    # 账号会话有效期（天）
    session_days: int = 30

    # 公开站点地址：邮件里的品牌图标等静态资源用它拼绝对 URL
    public_base_url: str = "http://154.40.58.230:8090"

    # AI 层（OpenAI 兼容接口；管理端 site_settings 优先于此处 env）
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
