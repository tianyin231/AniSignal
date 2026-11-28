import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { PLATFORM_META } from '../app/utils/lexicon.ts'
import { platformMeta } from '../app/utils/data.ts'

const WEB_APP_DIR = fileURLToPath(new URL('../app', import.meta.url))

function walkFiles(dir: string, exts: string[]): string[] {
  const result: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      result.push(...walkFiles(full, exts))
    } else if (exts.some(ext => full.endsWith(ext))) {
      result.push(full)
    }
  }
  return result
}

test('data.ts 平台元数据严格复用 lexicon.ts 单一真相源', () => {
  assert.strictEqual(platformMeta, PLATFORM_META)
})

test('Vue 组件不得重复维护平台或目标类型字典', () => {
  const forbidden = /\bconst\s+(?:PLATFORM_META|platformMeta|platformLabels|platformNames|adminPlatformLabels|targetTypeLabels|TARGET_TYPE_LABELS)\s*(?::[^=]+)?=\s*\{/
  const violations: string[] = []
  for (const file of walkFiles(WEB_APP_DIR, ['.vue'])) {
    const text = readFileSync(file, 'utf-8')
    if (forbidden.test(text)) {
      violations.push(relative(WEB_APP_DIR, file))
    }
  }
  assert.deepEqual(violations, [], `以下组件存在重复平台/目标类型字典，请改为从 ~/utils/lexicon 导入：${violations.join(', ')}`)
})

test('管理端页面禁止使用原生 alert() 弹窗', () => {
  const adminDir = join(WEB_APP_DIR, 'pages', 'admin')
  const violations: string[] = []
  for (const file of walkFiles(adminDir, ['.vue'])) {
    const text = readFileSync(file, 'utf-8')
    if (/(?:^|[^\w.$])alert\s*\(/m.test(text)) {
      violations.push(relative(WEB_APP_DIR, file))
    }
  }
  assert.deepEqual(violations, [], `以下管理端页面违规使用了 alert()：${violations.join(', ')}`)
})

test('Vue 模板不得重新引入已淘汰的 .panel-glass 样式类', () => {
  const violations: string[] = []
  for (const file of walkFiles(WEB_APP_DIR, ['.vue'])) {
    const text = readFileSync(file, 'utf-8')
    if (/\bpanel-glass\b/.test(text)) {
      violations.push(relative(WEB_APP_DIR, file))
    }
  }
  assert.deepEqual(violations, [], `以下模板使用了历史同义类 .panel-glass，请统一使用 .panel：${violations.join(', ')}`)
})
