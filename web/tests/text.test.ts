import test from 'node:test'
import assert from 'node:assert/strict'
import { decodeEntities } from '../app/utils/text.ts'

test('还原标题里的 HTML 实体，含二次转义', () => {
  assert.equal(decodeEntities('&quot;赛飞儿想变得可爱&quot;'), '"赛飞儿想变得可爱"')
  assert.equal(decodeEntities('开箱 试用感受&amp;amp;功能简介'), '开箱 试用感受&功能简介')
  assert.equal(decodeEntities('Kal&#39;tsit &#x2014; 新生'), 'Kal\'tsit — 新生')
})

test('不认识的实体与普通 & 原样保留', () => {
  assert.equal(decodeEntities('A & B'), 'A & B')
  assert.equal(decodeEntities('&unknown; &#0;'), '&unknown; &#0;')
})
