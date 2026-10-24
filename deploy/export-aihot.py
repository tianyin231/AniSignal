"""从旧 PostgreSQL 的一致性快照导出资讯数据，供统一数据库导入命令使用。"""

import argparse
import gzip
import json
import pathlib
import subprocess
from datetime import datetime, timezone

TABLES = (
    "sources", "articles", "article_revisions", "analyses", "publications",
    "stories", "facts", "fact_articles", "story_aliases", "story_links",
    "translations", "quote_translations", "reports", "report_revisions",
    "editorial_overrides", "grouping_overrides", "grouping_decisions",
    "receipts", "receipt_attempts", "selected_ledger", "selected_state",
    "settings", "budgets", "service_prices", "audit_log",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--container", default="anisignal-aihot-db-1")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    statement = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n" + "\n".join(
        f"SELECT json_build_object('table','{name}','row',to_jsonb(t)) FROM public.{name} t;"
        for name in TABLES
    ) + "\nCOMMIT;"
    process = subprocess.Popen(["docker", "exec", "-i", args.container, "psql", "-U", "aihot", "-d", "aihot",
        "-qAt", "-v", "ON_ERROR_STOP=1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    process.stdin.write(statement.encode())
    process.stdin.close()
    counts = {}
    try:
        with gzip.open(args.output, "wb") as dest:
            dest.write((json.dumps({"format": "anisignal-news-v1", "exported_at": datetime.now(timezone.utc).isoformat()}) + "\n").encode())
            for line in process.stdout:
                name = json.loads(line)["table"]
                counts[name] = counts.get(name, 0) + 1
                dest.write(line)
        if process.wait() != 0:
            raise RuntimeError("旧数据库导出失败；请保留原服务并重新导出")
        args.output.chmod(0o600)
        print(json.dumps({"file": str(args.output), "bytes": args.output.stat().st_size, "counts": counts}))
    finally:
        if process.poll() is None:
            process.terminate()


if __name__ == "__main__":
    main()
