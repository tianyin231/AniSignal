import test from 'node:test'
import assert from 'node:assert/strict'
import { apiErrorDetail } from '../app/utils/api-errors.ts'

test('apiErrorDetail 同时提取普通 HTTPException 字符串与 FastAPI 422 校验数组文案', () => {
  assert.equal(apiErrorDetail({ data: { detail: '分区已存在' } }, '失败'), '分区已存在')
  assert.equal(
    apiErrorDetail({ data: { detail: [{ msg: 'Value error, 分区标识仅支持 1~16 位小写字母' }] } }, '失败'),
    '分区标识仅支持 1~16 位小写字母',
  )
  assert.equal(apiErrorDetail(new Error('network'), '默认兜底'), '默认兜底')
})
