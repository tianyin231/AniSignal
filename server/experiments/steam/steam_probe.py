"""健康探针：四端点各 1 请求（无 community 端点）。JSONL 日志。"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("STEAM_HOME") or Path(__file__).resolve().parent)
sys.path.insert(0, str(ROOT))
from steam_collect import GAMES, client, fetch_app_detail, fetch_online, fetch_spy  # noqa: E402

LOG = ROOT / "stability" / "log.jsonl"
TEST_APP = GAMES["wuwa"][1]


def probe(label, fn):
    t0 = time.time()
    try:
        v = fn()
        return {"status": "ok" if v else "fail", "ms": int((time.time() - t0) * 1000)}
    except Exception:  # noqa: BLE001
        return {"status": "fail", "ms": int((time.time() - t0) * 1000)}


def main():
    LOG.parent.mkdir(exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat()}
    with client() as c:
        rec["detail"] = probe("detail", lambda: fetch_app_detail(c, TEST_APP))
        time.sleep(1.5)
        rec["online"] = probe("online", lambda: fetch_online(c, TEST_APP))
        time.sleep(1.5)
        rec["spy"] = probe("spy", lambda: fetch_spy(c, TEST_APP))
        time.sleep(1.5)
        rec["reviews"] = probe("reviews", lambda: (c.get(
            f"https://store.steampowered.com/ajaxappreviews/{TEST_APP}",
            params={"json": 1, "appids": TEST_APP, "language": "schinese", "filter": "recent",
                    "review_type": "all", "purchase_type": "all", "num_per_page": 1,
                    "start_offset": 0}).json().get("success") == 1))
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    cells = [f"{k}={rec[k]['status']}({rec[k]['ms']}ms)" for k in ("detail", "online", "spy", "reviews")]
    print(f"[{rec['ts'][:19]}] " + "  ".join(cells))


if __name__ == "__main__":
    main()
