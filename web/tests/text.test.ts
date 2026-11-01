import test from 'node:test'
import assert from 'node:assert/strict'
import { decodeEntities, isRedundantExcerpt } from '../app/utils/text.ts'

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
