"""Qdrant 单二进制进程管理：起服务、探活、停服务。

存储落在 data/qdrant/，pid 记在 data/qdrant.pid。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

from .config import DATA_DIR, KB_ROOT

log = logging.getLogger("kb.qdrant")

QDRANT_BIN = KB_ROOT / "vendor" / "qdrant"
STORAGE_DIR = DATA_DIR / "qdrant"
PID_FILE = DATA_DIR / "qdrant.pid"
PORT = int(os.environ.get("KB_QDRANT_PORT", "6333"))
BASE_URL = f"http://127.0.0.1:{PORT}"


def _write_config() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    # 手写 YAML，省一个 pyyaml 依赖
    (STORAGE_DIR / "config.yaml").write_text(
        "storage:\n"
        f"  storage_path: {STORAGE_DIR}\n"
        "service:\n"
        f"  http_port: {PORT}\n"
        f"  grpc_port: {PORT + 1}\n"
        "  enable_cors: true\n"
        "telemetry_disabled: true\n",
        encoding="utf-8",
    )


def is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/readyz", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _running_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def ensure_server() -> str:
    """保证 qdrant 在跑，返回 base url。"""
    if is_up():
        return BASE_URL
    pid = _running_pid()
    if pid:
        log.info("qdrant 进程 %d 存在但未就绪，先停掉", pid)
        _kill(pid)
    if not QDRANT_BIN.exists():
        raise RuntimeError(f"找不到 Qdrant 二进制：{QDRANT_BIN}（见 kb/README.md 下载方式）")
    _write_config()
    logf = open(DATA_DIR / "qdrant.log", "ab", buffering=0)
    # cwd 指向存储目录，免得 qdrant 在 kb/ 下乱建 snapshots/ 之类的目录
    proc = subprocess.Popen(
        [str(QDRANT_BIN), "--config-path", str(STORAGE_DIR / "config.yaml")],
        stdout=logf, stderr=subprocess.STDOUT, cwd=str(STORAGE_DIR),
    )
    PID_FILE.write_text(str(proc.pid))
    atexit.register(_cleanup)
    for _ in range(120):
        if is_up():
            log.info("Qdrant 已就绪：%s（pid %d）", BASE_URL, proc.pid)
            return BASE_URL
        if proc.poll() is not None:
            raise RuntimeError(f"Qdrant 启动即退出，见 {DATA_DIR / 'qdrant.log'}")
        time.sleep(0.5)
    raise RuntimeError("Qdrant 启动超时")


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.25)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def stop_server() -> None:
    pid = _running_pid()
    if pid:
        _kill(pid)
        log.info("Qdrant 已停止（pid %d）", pid)
    if PID_FILE.exists():
        PID_FILE.unlink()


def _cleanup() -> None:
    """进程退出时不杀 qdrant——它是常驻服务，下次直接复用。"""
    return


def collection_info(name: str) -> dict:
    ensure_server()
    req = urllib.request.Request(f"{BASE_URL}/collections/{name}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["result"]
