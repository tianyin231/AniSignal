import test from 'node:test'
import assert from 'node:assert/strict'
import {
  PALETTE_SWATCHES,
  buildDynamicColorCss,
  colorToHex,
  hexChannelThemeClass,
  hexCommunityClasses,
  parseHex6,
} from '../app/utils/community-palette.ts'
import { customKindLabel, customPlatformBadge } from '../app/utils/custom-feeds.ts'

test('调色板解析并规范化 #RRGGBB 颜色，拦截非法颜色字符串', () => {
  assert.equal(parseHex6('#EF4444'), 'ef4444')
  assert.equal(parseHex6(' #10b981 '), '10b981')
  assert.equal(parseHex6('sky'), null)
  assert.equal(parseHex6('#12345'), null)
  assert.equal(parseHex6('javascript:alert(1)'), null)

  assert.equal(colorToHex('#EF4444'), '#ef4444')
  assert.equal(colorToHex('violet'), '#7a5ae8')
  assert.equal(colorToHex('unknown'), '#2354f0')
  assert.ok(PALETTE_SWATCHES.length >= 16)
})

test('动态十六进制主题样式同时输出浅色与深色 CSS 规则并自动去重', () => {
  assert.deepEqual(hexCommunityClasses('ef4444'), {
    chip: 'c-chip-ef4444',
    nav: 'c-nav-ef4444',
    dot: 'c-dot-ef4444',
    text: 'c-text-ef4444',
  })
  assert.equal(hexChannelThemeClass('ef4444'), 'ch-hex-ef4444')

  const css = buildDynamicColorCss(['blue', '#EF4444', '#ef4444', null])
  assert.ok(css.includes('.ch-hex-ef4444{--sd-accent:#ef4444'))
  assert.ok(css.includes('.dark .ch-hex-ef4444,.ch-hex-ef4444.dark{'))
  assert.ok(css.includes('.c-chip-ef4444{'))
  assert.equal(css.split('.ch-hex-ef4444{').length - 1, 1)
})

test('定制监控类型与平台徽章支持 RSS 与 YouTube 频道', () => {
  assert.equal(customKindLabel('feed'), 'RSS 订阅')
  assert.equal(customKindLabel('channel_rss'), 'YouTube 频道')
  assert.equal(customPlatformBadge('rss', 'article'), 'RSS')
  assert.equal(customPlatformBadge('youtube', 'video'), 'YT')
})
