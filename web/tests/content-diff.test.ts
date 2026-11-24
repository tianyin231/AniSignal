import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  canCompareWithFirst,
  diffStatsBadges,
  normalizeVersionPair,
  originLabel,
  pairForVersionStep,
  revisionBadgeLabel,
} from '../app/utils/content-diff.ts'

describe('content-diff utils', () => {
  it('formats revision badge labels by origin and count', () => {
    assert.equal(revisionBadgeLabel(0, false), null)
    assert.equal(revisionBadgeLabel(undefined, true), null)
    assert.equal(revisionBadgeLabel(1, true), '原文修订')
    assert.equal(revisionBadgeLabel(3, true), '原文修订 ×3')
    assert.equal(revisionBadgeLabel(1, false), '站内修订')
    assert.equal(revisionBadgeLabel(2, false), '站内修订 ×2')
    assert.equal(originLabel('source'), '源站更新')
    assert.equal(originLabel('admin'), '站内修订')
  })

  it('builds compact stat badges only for non-zero deltas', () => {
    assert.deepEqual(
      diffStatsBadges({
        title_changed: true,
        added_lines: 2,
        removed_lines: 1,
        media_added: 1,
        media_removed: 0,
      }),
      [
        { key: 'title', label: '标题变更', tone: 'warn' },
        { key: 'add', label: '+2', tone: 'add' },
        { key: 'del', label: '-1', tone: 'del' },
        { key: 'media-add', label: '+1 图', tone: 'add' },
      ],
    )
    assert.deepEqual(diffStatsBadges(null), [])
  })

  it('handles version pair normalization and compare-with-first shortcut', () => {
    const versions = [
      { version: 1, origin: 'source' },
      { version: 2, origin: 'source' },
      { version: 4, origin: 'admin' },
    ]
    assert.equal(canCompareWithFirst(2, 4, versions), true)
    assert.equal(canCompareWithFirst(1, 4, versions), false)
    assert.equal(canCompareWithFirst(1, 2, versions.slice(0, 2)), false)

    assert.deepEqual(normalizeVersionPair(4, 1, versions), { from: 1, to: 4 })
    assert.equal(normalizeVersionPair(2, 2, versions), null)
    assert.equal(normalizeVersionPair(1, 99, versions), null)

    // 多版本快捷时间线：点首版比首→末，点第 k 版比 (k-1)→k
    assert.deepEqual(pairForVersionStep(1, versions), { from: 1, to: 4 })
    assert.deepEqual(pairForVersionStep(2, versions), { from: 1, to: 2 })
    assert.deepEqual(pairForVersionStep(4, versions), { from: 2, to: 4 })
    assert.equal(pairForVersionStep(99, versions), null)
  })
})
