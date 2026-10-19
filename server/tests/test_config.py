"""启动期配置校验：默认管理员令牌必须 fail-fast（SEC-001）。"""

import pytest

from app.config import Settings

def test_default_admin_token_is_rejected_outside_local_mode(monkeypatch):
    """SEC-001：非 local_mode 带着默认管理员令牌必须启动失败。

    原先 `admin_token` 默认 `change-me`，compose 再兜一次底，运维漏配环境变量时
    `/admin/*` 对一个写在公开仓库里的字符串开放，且无任何启动期提示。
    """
    from app.config import Settings

    for bad in ["change-me", "", "   "]:
        with pytest.raises(ValueError, match="ANISIGNAL_ADMIN_TOKEN"):
            Settings(admin_token=bad, local_mode=False)

    # 设了真令牌 → 正常
    assert Settings(admin_token="a" * 43, local_mode=False).admin_token == "a" * 43

    # local_mode 保持开箱即用（python -m app.local 不该被这条挡住）
    assert Settings(admin_token="change-me", local_mode=True).admin_token == "change-me"
