#!/usr/bin/env sh
# 把已提交的 DSH 插件目录同步到独立发布仓库 tianyin231/DSH_Plugin_AniSignal。
# DSH 插件支持直接从独立仓库根打包（tarball / git）安装，源码仍在本主仓库开发和统一测试。
#
# 用法：deploy/sync-dsh-plugin.sh <发布仓库的本地目录> [--push]
# 发版顺序：改 integrations/dsh_plugin_anisignal/package.json 与 lib/config.js 版本号 → 提交主仓库 → 运行本脚本。
set -eu

target=${1:?"用法: deploy/sync-dsh-plugin.sh <发布仓库目录> [--push]"}
push=${2:-}
plugin=integrations/dsh_plugin_anisignal

root=$(git rev-parse --show-toplevel)
cd "$root"
[ -d "$target/.git" ] || { echo "$target 不是 git 仓库" >&2; exit 1; }
if [ -n "$(git status --porcelain -- "$plugin" LICENSE)" ]; then
  echo "$plugin 有未提交的改动；脚本只同步已提交内容，请先提交。" >&2
  exit 1
fi
if [ -n "$(git -C "$target" status --porcelain)" ]; then
  echo "$target 有未提交的改动，先处理后再同步。" >&2
  exit 1
fi

commit=$(git rev-parse --short HEAD)
raw_ver=$(sed -n 's/.*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$plugin/package.json" | head -n 1)
version="v${raw_ver#v}"

# 先清空再整体导出，插件目录里删除的文件也会从发布仓库删除。
git -C "$target" rm -rq --ignore-unmatch -- .
git archive HEAD:"$plugin" | (cd "$target" && tar -xf -)
git archive HEAD LICENSE | (cd "$target" && tar -xf -)
git -C "$target" add -A

if git -C "$target" diff --cached --quiet; then
  echo "发布仓库已是 ${version}，无需同步。"
  exit 0
fi
git -C "$target" commit -qm "release: ${version}（AniSignal ${commit}）"
echo "已提交 ${version}（AniSignal ${commit}）到 $target"
if [ "$push" = "--push" ]; then
  git -C "$target" push origin HEAD
else
  echo "确认无误后推送：git -C \"$target\" push origin HEAD"
fi
