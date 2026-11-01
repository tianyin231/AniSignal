import assert from 'node:assert/strict'
import test from 'node:test'
import { loginRoute, safeRedirect } from '../app/utils/safe-redirect.ts'

const origin = 'https://anisignal.example'

test('登录回跳保留正常站内路径、查询参数和锚点', () => {
  for (const path of ['/account', '/contents/1?tab=all#source', '/events/../account']) {
    const result = safeRedirect(path, origin)
    assert.equal(new URL(result, origin).origin, origin)
    assert.equal(result, new URL(path, origin).pathname + new URL(path, origin).search + new URL(path, origin).hash)
  }
})

test('登录回跳拒绝站外地址及反斜杠、控制符和点段归一化绕过', () => {
  for (const path of [
    '//evil.example', '/\\evil.example', '/..//evil.example', '/.//evil.example',
    '/%2e%2e//evil.example', '/\t/evil.example', '/\n/evil.example',
    'https://evil.example', 'javascript:alert(1)', '', null, ['/account'],
  ]) {
    assert.equal(safeRedirect(path, origin), '/account', String(path))
  }
  assert.equal(safeRedirect('/events', ''), '/account')
})

test('去登录页时记住来源页，首页和认证页本身不记', () => {
	assert.deepEqual(loginRoute('/custom/3?page=2'), { path: '/login', query: { redirect: '/custom/3?page=2' } })
	assert.deepEqual(loginRoute('/account?tab=notify'), { path: '/login', query: { redirect: '/account?tab=notify' } })
	assert.deepEqual(loginRoute('/'), { path: '/login' })
	assert.deepEqual(loginRoute('/login?redirect=%2Fwatch'), { path: '/login' })
	assert.deepEqual(loginRoute('/register'), { path: '/login' })
	assert.deepEqual(loginRoute(), { path: '/login' })
})
