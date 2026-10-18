import test from 'node:test'
import assert from 'node:assert/strict'
import { imageSrcset, imageUrl } from '../app/utils/image.ts'

test('B 站展示图按尺寸请求 WebP，替换已有缩放参数并保留查询参数', () => {
  assert.equal(imageUrl('https://i2.hdslb.com/bfs/archive/cover.jpg@640w.jpg?x=1', 160),
    'https://i2.hdslb.com/bfs/archive/cover.jpg@160w.webp?x=1')
  assert.equal(imageSrcset('https://i0.hdslb.com/bfs/new_dyn/cover.png', [160, 320]),
    'https://i0.hdslb.com/bfs/new_dyn/cover.png@160w.webp 160w, https://i0.hdslb.com/bfs/new_dyn/cover.png@320w.webp 320w')
})

test('其他图床保留原链接，不套用 B 站的缩放协议', () => {
  const url = 'https://i.ytimg.com/vi/example/maxresdefault.jpg'
  assert.equal(imageUrl(url, 160), url)
  assert.equal(imageSrcset(url, [160, 320]), undefined)
  assert.equal(imageUrl(undefined, 160), undefined)
})
