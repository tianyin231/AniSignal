/** 内容版本修订与 Diff 展示纯计算工具（含 node --test 单测）。 */

export interface DiffStatsLike {
  added_lines: number
  removed_lines: number
  title_changed: boolean
  media_added: number
  media_removed: number
}

export interface DiffStatBadge {
  key: string
  label: string
  tone: 'add' | 'del' | 'warn' | 'neutral'
}

export interface VersionMetaLike {
  version: number
  origin: string
}

/** 列表与详情页通用的修订标识文案；无实质修订时返回 null。 */
export function revisionBadgeLabel(
  revisionCount: number | null | undefined,
  hasSourceRevision: boolean | null | undefined,
): string | null {
  const count = Number(revisionCount ?? 0)
  if (!Number.isFinite(count) || count <= 0) return null
  const prefix = hasSourceRevision ? '原文修订' : '站内修订'
  return count > 1 ? `${prefix} ×${count}` : prefix
}

export function originLabel(origin: string | null | undefined): string {
  return origin === 'admin' ? '站内修订' : '源站更新'
}

/** 将 Diff 统计转为简洁的视觉徽章列表，避免冗余说明文案。 */
export function diffStatsBadges(stats: DiffStatsLike | null | undefined): DiffStatBadge[] {
  if (!stats) return []
  const badges: DiffStatBadge[] = []
  if (stats.title_changed) {
    badges.push({ key: 'title', label: '标题变更', tone: 'warn' })
  }
  if (stats.added_lines > 0) {
    badges.push({ key: 'add', label: `+${stats.added_lines}`, tone: 'add' })
  }
  if (stats.removed_lines > 0) {
    badges.push({ key: 'del', label: `-${stats.removed_lines}`, tone: 'del' })
  }
  if (stats.media_added > 0) {
    badges.push({ key: 'media-add', label: `+${stats.media_added} 图`, tone: 'add' })
  }
  if (stats.media_removed > 0) {
    badges.push({ key: 'media-del', label: `-${stats.media_removed} 图`, tone: 'del' })
  }
  return badges
}

/** 判断当前对比是否可一键切到“对比首版”。 */
export function canCompareWithFirst(
  fromVersion: number | null | undefined,
  toVersion: number | null | undefined,
  versions: readonly VersionMetaLike[],
): boolean {
  if (versions.length < 3 || !fromVersion || !toVersion) return false
  const first = versions[0]?.version
  return first != null && fromVersion !== first && toVersion !== first
}

/** 根据用户选择的基准/目标版本做合法规范化（自动保证 from < to）。 */
export function normalizeVersionPair(
  fromVersion: number,
  toVersion: number,
  versions: readonly VersionMetaLike[],
): { from: number; to: number } | null {
  const allowed = new Set(versions.map(v => v.version))
  if (!allowed.has(fromVersion) || !allowed.has(toVersion) || fromVersion === toVersion) {
    return null
  }
  return fromVersion < toVersion
    ? { from: fromVersion, to: toVersion }
    : { from: toVersion, to: fromVersion }
}

/** 点击多版本时间线上的某个版本节点时，计算要展示的对比区间：
 *  - 点击首版：对比「首版 → 最新版」总览；
 *  - 点击第 k 版（k > 0）：对比「上一版 → 第 k 版」增量。
 */
export function pairForVersionStep(
  clickedVersion: number,
  versions: readonly VersionMetaLike[],
): { from: number; to: number } | null {
  if (versions.length < 2) return null
  const idx = versions.findIndex(v => v.version === clickedVersion)
  if (idx < 0) return null
  if (idx === 0) {
    return { from: versions[0]!.version, to: versions[versions.length - 1]!.version }
  }
  return { from: versions[idx - 1]!.version, to: versions[idx]!.version }
}
