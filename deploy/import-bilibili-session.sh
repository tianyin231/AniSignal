#!/bin/sh
# 非 root 升级后迁移旧文件：临时以 root 读取原有只读挂载；业务进程仍以普通用户运行。
# 已有后台凭据时安全跳过；不会输出 Cookie 或修改原文件权限。
set -eu
cd "$(dirname "$0")/.."
exec docker compose -p anisignal -f compose.yaml -f deploy/compose.ty.yaml \
  exec -T --user root api python -m app.bilibili_login \
  --import-to-db --cookie-file /run/anisignal/bilibili/cookies.txt
