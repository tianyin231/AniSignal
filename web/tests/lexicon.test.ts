import test from 'node:test'
import assert from 'node:assert/strict'
import {
  ADMIN_NAV_GROUPS,
  ADMIN_PLATFORM_LABELS,
  CUSTOM_KIND_LABELS,
  MONITOR_GROUPS,
  MONITOR_KINDS,
  PLATFORM_META,
  TARGET_TYPE_LABELS,
  platformLabel,
  platformRegion,
  tierLabel,
  webExtractorLabel,
} from '../app/utils/lexicon.ts'
import { customFeedBadge, customKindLabel } from '../app/utils/custom-feeds.ts'

test('核心平台与目标类型遵循统一书面规范', () => {
  assert.equal(platformLabel('bilibili'), 'B 站')
  assert.equal(platformLabel('rss'), 'RSS 订阅')
  assert.equal(platformLabel('web_page'), '网页监测')
  assert.equal(platformLabel('game_official'), '游戏官网')
  assert.equal(ADMIN_PLATFORM_LABELS.rss, 'RSS 订阅')
  assert.equal(ADMIN_PLATFORM_LABELS.game_official, '游戏官网')
  assert.equal(TARGET_TYPE_LABELS.page_watch, '单页变更')
  assert.equal(TARGET_TYPE_LABELS.list_watch, '列表更新')
  assert.equal(TARGET_TYPE_LABELS.feed, 'RSS 订阅')
})

test('定制监控类型统一且所有监控项都归属有效分组', () => {
  const groupKeys = new Set(MONITOR_GROUPS.map(g => g.key))
  for (const kind of MONITOR_KINDS) {
    assert.ok(groupKeys.has(kind.group), `未知分组: ${kind.group}`)
    assert.ok(kind.platform in PLATFORM_META, `未知平台: ${kind.platform}`)
    assert.ok(kind.desc.length > 0 && kind.desc.length <= 16, `决策副标过长: ${kind.desc}`)
  }
  assert.equal(customKindLabel('page_watch'), '单页变更')
  assert.equal(customKindLabel('list_watch'), '列表更新')
  assert.equal(customKindLabel('feed'), 'RSS 订阅')
  assert.equal(CUSTOM_KIND_LABELS.uid, 'B 站 UP 主')
  assert.equal(customFeedBadge('web_page').label, '网页监测')
  assert.equal(customFeedBadge('rss').label, 'RSS 订阅')
})

test('收录策略、区域与网页解析模式映射准确', () => {
  assert.equal(tierLabel('core'), '全部收录')
  assert.equal(tierLabel('general'), '条件筛选')
  assert.equal(platformRegion('kuro_global'), '国际服')
  assert.equal(platformRegion('web_page'), '通用')
  assert.equal(webExtractorLabel('trafilatura'), '正文提取')
  assert.equal(webExtractorLabel('css_selector'), '指定区域')
  assert.equal(webExtractorLabel('list_discovery'), '列表解析')
})

test('后台分组导航包含 4 组且路径无重复', () => {
  assert.equal(ADMIN_NAV_GROUPS.length, 4)
  const paths = ADMIN_NAV_GROUPS.flatMap(g => g.items.map(i => i.to))
  assert.equal(paths.length, 13)
  assert.equal(new Set(paths).size, 13)
})
