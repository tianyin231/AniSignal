import test from 'node:test'
import assert from 'node:assert/strict'
import { imageSrcset, imageUrl } from '../app/utils/image.ts'

test('B 站展示图按尺寸请求 WebP，替换已有缩放参数并保留查询参数', () => {
  assert.equal(imageUrl('https://i2.hdslb.com/bfs/archive/cover.jpg@640w.jpg?x=1', 160),
    'https://i2.hdslb.com/bfs/archive/cover.jpg@160w.webp?x=1')
  assert.equal(imageSrcset('https://i0.hdslb.com/bfs/new_dyn/cover.png', [160, 320]),
    'https://i0.hdslb.com/bfs/new_dyn/cover.png@160w.webp 160w, https://i0.hdslb.com/bfs/new_dyn/cover.png@320w.webp 320w')
})

test('B 站 http 封面升级为 https，避免混合内容', () => {
  assert.equal(imageUrl('http://i1.hdslb.com/bfs/archive/cover.jpg', 320),
    'https://i1.hdslb.com/bfs/archive/cover.jpg@320w.webp')
})

test('YouTube 小图用 mqdefault，大图保留原链接', () => {
  const url = 'https://i.ytimg.com/vi/rqG4JM9oevE/maxresdefault.jpg'
  assert.equal(imageUrl(url, 320), 'https://i.ytimg.com/vi/rqG4JM9oevE/mqdefault.jpg')
  assert.equal(imageUrl(url, 1600), url)
  // 两档都落在 mqdefault 时没有可选项，不输出 srcset
  assert.equal(imageSrcset(url, [160, 320]), undefined)
  assert.equal(imageSrcset(url, [320, 1600]), `https://i.ytimg.com/vi/rqG4JM9oevE/mqdefault.jpg 320w, ${url} 1280w`)
})

test('X 图床按档位替换 name 参数并保留 format', () => {
  const url = 'https://pbs.twimg.com/media/HV9QG50WcAAL_gV?format=webp&name=large'
  assert.equal(imageUrl(url, 160), 'https://pbs.twimg.com/media/HV9QG50WcAAL_gV?format=webp&name=360x360')
  assert.equal(imageUrl(url, 640), 'https://pbs.twimg.com/media/HV9QG50WcAAL_gV?format=webp&name=small')
  assert.equal(imageSrcset(url, [640, 960, 1600]),
    'https://pbs.twimg.com/media/HV9QG50WcAAL_gV?format=webp&name=small 680w, '
    + 'https://pbs.twimg.com/media/HV9QG50WcAAL_gV?format=webp&name=medium 1200w, '
    + 'https://pbs.twimg.com/media/HV9QG50WcAAL_gV?format=webp&name=large 2048w')
  // 头像等非媒体路径不处理
  const avatar = 'https://pbs.twimg.com/profile_images/1/abc_normal.jpg'
  assert.equal(imageUrl(avatar, 160), avatar)
})

test('米哈游 / 鹰角 OSS 图床使用 x-oss-process 缩放', () => {
  assert.equal(imageUrl('https://fastcdn.mihoyo.com/content-v2/hk4e/1/a.jpg', 320),
    'https://fastcdn.mihoyo.com/content-v2/hk4e/1/a.jpg?x-oss-process=image%2Fresize%2Cw_320%2Fformat%2Cwebp')
  // 已带处理参数的地址不重复套用
  const processed = 'https://upload-bbs.miyoushe.com/upload/a.jpg?x-oss-process=image/resize,s_600'
  assert.equal(imageUrl(processed, 320), processed)
})

test('未知图床保留原链接', () => {
  const url = 'https://cdn.arstechnica.net/wp-content/uploads/2026/10/cover-1152x648.jpg'
  assert.equal(imageUrl(url, 160), url)
  assert.equal(imageSrcset(url, [160, 320]), undefined)
  assert.equal(imageUrl(undefined, 160), undefined)
  assert.equal(imageUrl('not a url', 160), 'not a url')
})
