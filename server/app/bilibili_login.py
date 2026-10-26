"""正常网页扫码登录：python -m app.bilibili_login --cookie-file PATH --qr-image PATH。

仅调用B站扫码授权接口；用户在官方 App 确认后才原子替换本地会话文件。
"""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import time

import httpx
import qrcode

from app.services.bilibili_session import cookie_header

PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode/"


def save_cookies(path: Path, cookies: dict) -> None:
    if not cookies.get("SESSDATA"):
        raise ValueError("登录响应缺少会话凭据，未保存")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".session-", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(cookie_header(cookies) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def login(cookie_file: Path, qr_image: Path) -> bool:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with httpx.AsyncClient(timeout=15, trust_env=False, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/",
    }) as client:
        response = await client.get(PASSPORT + "generate")
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise ValueError("B站暂不接受扫码登录，请稍后操作")
        data = payload["data"]
        qr_image.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with qr_image.open("wb") as stream:
            os.chmod(qr_image, 0o600)
            qrcode.make(data["url"]).save(stream, format="PNG")
        print(json.dumps({"state": "waiting", "qr_image": str(qr_image)}, ensure_ascii=False), flush=True)
        deadline = time.monotonic() + 180
        scanned = False
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            response = await client.get(PASSPORT + "poll", params={"qrcode_key": data["qrcode_key"]})
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise ValueError("B站拒绝登录检测，已停止轮询")
            code = (payload.get("data") or {}).get("code")
            if code == 0:
                cookies = {cookie.name: cookie.value for cookie in client.cookies.jar
                    if cookie.domain.lstrip(".") in {"bilibili.com", "passport.bilibili.com"}}
                save_cookies(cookie_file, cookies)
                print(json.dumps({"state": "saved", "message": "授权完成，凭据已私有保存"}, ensure_ascii=False), flush=True)
                qr_image.unlink(missing_ok=True)
                return True
            if code == 86038:
                break
            if code == 86090 and not scanned:
                scanned = True
                print(json.dumps({"state": "scanned", "message": "请在B站 App 确认登录"}, ensure_ascii=False), flush=True)
            if code not in (86090, 86101):
                raise ValueError("B站要求额外验证，请在官方客户端完成后重新操作")
    qr_image.unlink(missing_ok=True)
    print(json.dumps({"state": "expired", "message": "二维码已过期，现有凭据保持原样"}, ensure_ascii=False), flush=True)
    return False


def main():
    parser = argparse.ArgumentParser(description="B站官方网页扫码授权，不输出会话凭据")
    parser.add_argument("--cookie-file", type=Path, required=True)
    parser.add_argument("--qr-image", type=Path, required=True)
    args = parser.parse_args()
    try:
        success = asyncio.run(login(args.cookie_file, args.qr_image))
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        # 网络异常的 URL/响应可能携带授权信息，控制台只给操作提示。
        print(json.dumps({"state": "error", "message": "登录未完成，请检查网络或重新授权", "error_type": type(exc).__name__}, ensure_ascii=False), flush=True)
        raise SystemExit(1) from None
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
