/** 公开站共享工具：平台→区域、类目元数据、API→UI 适配 */

import { fmtTime } from '~/utils/admin-types'
import { type Identity, type Platform, type RawItem, type Region, type AppEvent, type SourceRef } from '~/utils/data'
import type { ApiEvent, ApiProduct, PublicContentItem } from '~/composables/usePublicApi'

export function utcTimestamp(value: string | null | undefined): number {
  if (!value) return 0
  return new Date(/Z$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`).getTime()
}

export const regionOf = (platform: string): Region =>
  ['kuro_global', 'youtube'].includes(platform) ? 'global' : 'cn'

export const categoryMeta: Record<string, { label: string }> = {
  official_announce: { label: '公告' },
  version_update: { label: '版本' },
  presale: { label: '预售' },
  balance: { label: '尾款' },
  delay: { label: '延期' },
  shipping: { label: '发货' },
  arrived: { label: '到货' },
  unboxing: { label: '开箱' },
  review: { label: '评测' },
  quality: { label: '质量' },
  promotion: { label: '推广' },
  other: { label: '其他' },
}

const CONFIDENCE_LABEL: Record<string, '高' | '中' | '低'> = {
  high: '高',
  medium: '中',
  low: '低',
}

export function toAppEvent(e: ApiEvent): AppEvent {
  return {
    id: String(e.id),
    title: e.title,
    summary: e.summary ?? e.conclusion ?? '',
    category: categoryMeta[e.category]?.label ?? e.category,
    identity: e.identity as Identity,
    regions: (e.regions ?? []) as Region[],
    sources: e.sources.map(
      (s): SourceRef => ({
        platform: s.platform as Platform,
        account: s.account ?? '',
        region: s.region as Region,
        publishedAt: fmtTime(s.published_at),
        foundAt: '',
        url: s.url ?? undefined,
        contentId: (s as any).content_id ?? null,
      }),
    ),
    evidenceCount: e.evidence_count,
    community: e.community,
    firstSeenAt: fmtTime(e.first_seen_at),
    latestEvidenceAt: fmtTime(e.latest_evidence_at ?? e.first_seen_at),
    updatedAt: fmtTime(e.updated_at),
    important: e.importance || undefined,
    confidence: CONFIDENCE_LABEL[e.confidence] ?? '中',
    conclusion: e.conclusion ?? '',
    statusChange: e.status_label ?? undefined,
    facts: e.facts ?? [],
    matchedKeywords: e.matched_keywords ?? [],
    corrections: (e.corrections ?? []).map((c) => ({
      at: fmtTime(c.at),
      note: `${c.actor === 'admin' ? '人工' : '引擎'}：${c.note}`,
    })),
  }
}

export function toRawItem(item: PublicContentItem): RawItem {
  return {
    id: String(item.id),
    platform: item.platform as Platform,
    account: item.source_name ?? '—',
    region: regionOf(item.platform),
    title: item.title || item.external_id,
    publishedAt: fmtTime(item.published_at || item.collected_at),
    identity: item.identity as Identity,
    category: item.category ? (categoryMeta[item.category]?.label ?? item.category) : undefined,
    keywords: item.matched_keywords ?? [],
    excerpt: item.text_excerpt ?? undefined,
    cover: item.cover ?? undefined,
    originalUrl: item.url ?? undefined,
    metrics: item.metrics ?? undefined,
    community: item.community,
    previewMode: item.preview_mode,
  }
}
