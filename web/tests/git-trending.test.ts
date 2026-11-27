import test from 'node:test'
import assert from 'node:assert/strict'
import {
  formatCompactStars,
  formatPeriodKey,
  languageColor,
  periodGainLabel,
  splitRepoName,
  trendingEntryToRawItem,
  trendingStatusBadge,
} from '../app/utils/git-trending.ts'

test('GitHub Trending 刊期键与星标数格式化准确', () => {
  assert.equal(formatPeriodKey('daily', '2026-11-26'), '2026/11/26')
  assert.equal(formatPeriodKey('weekly', '2026-W48'), '2026 第 48 周')
  assert.equal(formatPeriodKey('monthly', '2026-11'), '2026 年 11 月')

  assert.equal(formatCompactStars(950), '950')
  assert.equal(formatCompactStars(24042), '24.0k')
  assert.equal(formatCompactStars(125300), '125k')
  assert.equal(periodGainLabel('daily'), '今日新增')
  assert.equal(periodGainLabel('weekly'), '本周新增')
  assert.equal(periodGainLabel('monthly'), '本月新增')
})

test('仓库名拆解、语言色点与跨期上榜标记生成准确', () => {
  assert.deepEqual(splitRepoName('tianyin231/AniSignal'), { owner: 'tianyin231', name: 'AniSignal' })
  assert.deepEqual(splitRepoName('standalone'), { owner: '', name: 'standalone' })
  assert.equal(languageColor('Python'), '#3572A5')
  assert.equal(languageColor('UnknownLang'), '#64748B')

  assert.deepEqual(
    trendingStatusBadge({ status: 'new', streak_count: 1, appearances: 1 }, 'daily'),
    { label: '首次上榜', tone: 'new' },
  )
  assert.deepEqual(
    trendingStatusBadge({ status: 'streak', streak_count: 3, appearances: 3 }, 'daily'),
    { label: '连榜 3 天', tone: 'streak' },
  )
  assert.deepEqual(
    trendingStatusBadge({ status: 'streak', streak_count: 2, appearances: 2 }, 'weekly'),
    { label: '连榜 2 周', tone: 'streak' },
  )
  assert.deepEqual(
    trendingStatusBadge({ status: 'returning', streak_count: 1, appearances: 4 }, 'monthly'),
    { label: '复榜 · 第 4 次', tone: 'returning' },
  )

  const raw = trendingEntryToRawItem({
    rank: 1,
    repo: 'acme/vision',
    url: 'https://github.com/acme/vision',
    description: 'Vision toolkit',
    language: 'Python',
    stars: 9800,
    forks: 420,
    period_stars: 1200,
    raw_content_id: 42,
    status: 'new',
    streak_count: 1,
    appearances: 1,
    first_seen_key: '2026-11-27',
    best_rank: 1,
    rank_delta: null,
    also_in: ['weekly'],
    brief: {
      kind: 'repo',
      headline: '视觉智能体工具集',
      tagline: '开源多模态视觉框架',
      summary: '完整介绍',
      highlights: [],
      use_cases: [],
      tags: ['AI', '视觉'],
    },
  })
  assert.equal(raw.id, '42')
  assert.equal(raw.platform, 'github')
  assert.equal(raw.contentType, 'repo')
  assert.deepEqual(raw.metrics, { view: 1200, like: 9800, fork: 420 })
  assert.deepEqual(raw.topics, ['AI', '视觉'])
  assert.equal(raw.previewMode, 'full')
})
