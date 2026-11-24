#!/usr/bin/env sh
# 把已提交的 DSH 插件目录同步到独立发布仓库 tianyin231/DSH_Plugin_AniSignal。
# DSH 插件支持直接从独立仓库根打包（tarball / git）安装，源码仍在本主仓库开发和统一测试。
#
# 用法：deploy/sync-dsh-plugin.sh <发布仓库的本地目录> [--push]
# 发版顺序：更新 package.json、package-lock.json、lib/config.js 与 CHANGELOG.md → 提交主仓库 → 运行本脚本。
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
raw_ver=$(sed -n 's/.*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$plugin/package.json" | head -n 1 | tr -d '\r')
version="v${raw_ver#v}"
changelog="$plugin/CHANGELOG.md"
if [ ! -f "$changelog" ]; then
  echo "缺少 $changelog，请先补充 ${version} 的更新日志后再发版。" >&2
  exit 1
fi

notes=$(awk -v prefix="## $version" '
  { sub(/\r$/, "") }
  !found && substr($0, 1, length(prefix)) == prefix && (length($0) == length(prefix) || substr($0, length(prefix) + 1, 1) ~ /[[:space:]]/) { found=1; next }
  found && /^## / { exit }
  found { print }
' "$changelog" | sed -e '/./,$!d')

if [ -z "$notes" ]; then
  echo "$changelog 中未找到「## ${version}」更新日志条目，请补充后再同步。" >&2
  exit 1
fi

# 先清空再整体导出，插件目录里删除的文件也会从发布仓库删除。
git -C "$target" rm -rq --ignore-unmatch -- .
git archive HEAD:"$plugin" | (cd "$target" && tar -xf -)
git archive HEAD LICENSE | (cd "$target" && tar -xf -)
git -C "$target" add -A

if git -C "$target" diff --cached --quiet; then
  echo "发布仓库已是 ${version}，无需同步。"
else
  git -C "$target" commit -q -m "release: ${version}（AniSignal ${commit}）" -m "$notes"
  echo "已提交 ${version}（AniSignal ${commit}）到 $target"
fi

if [ "$push" = "--push" ]; then
  git -C "$target" push origin HEAD
  if command -v gh >/dev/null 2>&1; then
    repo=$(git -C "$target" remote get-url origin | sed -E 's#.*github\.com[:/]##; s#\.git$##')
    release_sha=$(git -C "$target" rev-parse HEAD)
    if gh release view "$version" -R "$repo" >/dev/null 2>&1; then
      gh release edit "$version" -R "$repo" --title "$version" --notes "$notes" --target "$release_sha" >/dev/null
      echo "已更新 GitHub Release $repo@$version"
    else
      gh release create "$version" -R "$repo" --title "$version" --notes "$notes" --target "$release_sha" >/dev/null
      echo "已发布 GitHub Release $repo@$version"
    fi
  fi
else
  echo "确认无误后推送：git -C \"$target\" push origin HEAD"
fi
