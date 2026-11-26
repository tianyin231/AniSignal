import test from 'node:test'
import assert from 'node:assert/strict'
import { cleanPlatformText, cleanPlatformTitle, decodeEntities, isRedundantExcerpt, parseMarkdownBlocks } from '../app/utils/text.ts'

test('还原标题里的 HTML 实体，含二次转义', () => {
  assert.equal(decodeEntities('&quot;赛飞儿想变得可爱&quot;'), '"赛飞儿想变得可爱"')
  assert.equal(decodeEntities('开箱 试用感受&amp;amp;功能简介'), '开箱 试用感受&功能简介')
  assert.equal(decodeEntities('Kal&#39;tsit &#x2014; 新生'), 'Kal\'tsit — 新生')
})

test('不认识的实体与普通 & 原样保留', () => {
  assert.equal(decodeEntities('A & B'), 'A & B')
  assert.equal(decodeEntities('&unknown; &#0;'), '&unknown; &#0;')
})

test('摘要只是重复标题时视为冗余', () => {
  assert.equal(isRedundantExcerpt('潮起云涌', '潮起云涌'), true)
  assert.equal(isRedundantExcerpt(
    'Winners Announcement The event has concluded. Thank you for participating, and congratulations to all the lucky Endministrators! You can check the Winners\' Lis',
    'Winners Announcement The event has concluded. Thank you for participating, and congratulations to all the lucky Endministrators! You can check the Winners\' List here.',
  ), true)
})

test('标题只是正文开头几个字时保留摘要', () => {
  assert.equal(isRedundantExcerpt('【新增家具】', '【新增家具】 本周新增「雪夜小屋」系列家具，可在商店兑换。'), false)
  assert.equal(isRedundantExcerpt('版本更新维护预告', '版本更新维护预告：11 月 5 日 06:00 起停服维护，预计 5 小时，期间无法登录游戏，补偿 300 星琼。'), false)
  assert.equal(isRedundantExcerpt('标题', undefined), false)
})

test('cleanPlatformTitle 与 cleanPlatformText 清理小黑盒原始表情占位码并兜底空结果', () => {
  assert.equal(cleanPlatformTitle('感谢小黑盒[cube_喜欢]'), '感谢小黑盒')
  assert.equal(cleanPlatformTitle('掉闪卡了[cube_欧润吉]讲讲通关想法'), '掉闪卡了 讲讲通关想法')
  assert.equal(cleanPlatformTitle('[cube_喜欢]'), '[cube_喜欢]')
  assert.equal(cleanPlatformText('太香了[cube_doge]还有[heygirl_rua!]测试'), '太香了还有测试')
})

test('parseMarkdownBlocks 正确解析标题、列表、引用与代码块且不输出 raw HTML', () => {
  const blocks = parseMarkdownBlocks([
    '# Project Title',
    '',
    'See [Docs](https://example.com) for **details**.',
    '',
    '> Important note',
    '',
    '- Feature A',
    '- Feature B',
    '',
    '```bash',
    'npm run build',
    '```',
  ].join('\n'))

  assert.equal(blocks.length, 5)
  assert.deepEqual(blocks[0], { type: 'heading', level: 1, text: 'Project Title' })
  assert.deepEqual(blocks[1], { type: 'paragraph', text: 'See Docs for details.' })
  assert.deepEqual(blocks[2], { type: 'quote', text: 'Important note' })
  assert.deepEqual(blocks[3], { type: 'list', items: ['Feature A', 'Feature B'] })
  assert.deepEqual(blocks[4], { type: 'code', lang: 'bash', text: 'npm run build' })
})
