"""从现有 app 的配置生成独立引擎环境；stdout 只报告状态，永不打印凭据。"""
import json
import os
from pathlib import Path
import secrets
import subprocess

root = Path(__file__).resolve().parent.parent
target = root / ".env.aihot"
if target.exists():
    raise SystemExit(".env.aihot 已存在，保留当前配置")
read_config = """import asyncio,json
from app.db import SessionLocal
from app.services.ai import get_ai_config
from app.config import get_settings
async def main():
    async with SessionLocal() as session: print(json.dumps({**await get_ai_config(session), 'site_url': get_settings().public_base_url}))
asyncio.run(main())"""
result = subprocess.run(["docker", "compose", "exec", "-T", "app", "python", "-c", read_config],
    cwd=root, text=True, capture_output=True, check=True)
config = json.loads(result.stdout)
password, token = secrets.token_hex(28), secrets.token_hex(32)
values = {
    "POSTGRES_PASSWORD": password, "DATABASE_URL": f"postgres://aihot:{password}@aihot-db:5432/aihot",
    "NODE_ENV": "production", "AIHOT_ENVIRONMENT": "production",
    "SITE_URL": config.get("site_url") or os.environ.get("ANISIGNAL_PUBLIC_BASE_URL", "http://localhost:8090"),
    "AIHOT_RELEASE": "a0d1e75-anisignal", "SESSION_SECRET": secrets.token_hex(32),
    "IMG_PROXY_SIGN_SECRET": secrets.token_hex(32), "ADMIN_PASSWORD": secrets.token_hex(24),
    "ANISIGNAL_BRIDGE_TOKEN": token, "ANISIGNAL_AIHOT_TOKEN": token,
    "ANISIGNAL_AIHOT_URL": "http://aihot-api:3001", "ANISIGNAL_AIHOT_READ_ENABLED": "false",
    "ANISIGNAL_AIHOT_BOT_AFTER": "", "MODEL_CALLS_ENABLED": "false", "COLLECT_ENABLED": "false",
    "FEISHU_CONTENT_PUSH_ENABLED": "false", "INDEXNOW_SUBMIT_ENABLED": "false",
    "LLM_BASE_URL": config["base_url"], "LLM_API_KEY": config.get("api_key") or (config.get("api_keys") or [""])[0],
    "LLM_MODEL": config["model"], "LLM_JSON_MODE": "true", "LLM_VISION": "false",
}
if any("\n" in str(value) or "\r" in str(value) for value in values.values()):
    raise SystemExit("配置中存在非法换行")
target.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
target.chmod(0o600)
print("已生成 .env.aihot；采集、模型调用及公开切换默认关闭")
