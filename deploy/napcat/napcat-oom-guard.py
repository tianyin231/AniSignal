#!/usr/bin/env python3
"""给运行中的 NapCat 应用 OOM 优先级和整组恢复保护。"""

import json
import os
import subprocess
from pathlib import Path


def main():
    container = os.environ.get("NAPCAT_CONTAINER", "napcat")
    result = subprocess.run(
        ["docker", "inspect", container],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        return
    state = json.loads(result.stdout)[0]["State"]
    if not state["Running"] or state["Restarting"]:
        return

    try:
        membership = Path(f"/proc/{state['Pid']}/cgroup").read_text()
        relative = next(line[3:] for line in membership.splitlines() if line.startswith("0::"))
        cgroup = Path("/sys/fs/cgroup") / relative.lstrip("/")
        # QQ 子进程超限时整组退出，交给 Docker 的 restart: always 恢复。
        group = cgroup / "memory.oom.group"
        if group.read_text().strip() != "1":
            group.write_text("1\n")
        for pid in (cgroup / "cgroup.procs").read_text().split():
            try:
                score = Path(f"/proc/{pid}/oom_score_adj")
                if int(score.read_text()) > -300:
                    score.write_text("-300\n")
            except (FileNotFoundError, ProcessLookupError):
                pass  # 短命子进程可能已退出。
    except (FileNotFoundError, ProcessLookupError):
        pass  # 与正常重启同时执行时，下次定时器会重新应用。


if __name__ == "__main__":
    main()
