import assert from 'node:assert/strict'
import test from 'node:test'
import { copyText, safeRandomId } from '../app/utils/clipboard.ts'

test('safeRandomId 在有或无 crypto.randomUUID 时都能返回非空唯一标识', () => {
  const id1 = safeRandomId()
  const id2 = safeRandomId()
  assert.ok(id1.length >= 8)
  assert.notEqual(id1, id2)

  const original = globalThis.crypto?.randomUUID
  try {
    if (globalThis.crypto) {
      Object.defineProperty(globalThis.crypto, 'randomUUID', { value: undefined, configurable: true })
    }
    const fallbackId = safeRandomId()
    assert.ok(fallbackId.includes('-'))
    assert.ok(fallbackId.length >= 10)
  } finally {
    if (globalThis.crypto && original) {
      Object.defineProperty(globalThis.crypto, 'randomUUID', { value: original, configurable: true })
    }
  }
})

test('copyText 在无 DOM/SSR 环境安全返回 false 而非抛错', async () => {
  const ok = await copyText('ANISIGNAL2026')
  assert.equal(ok, false)
})
