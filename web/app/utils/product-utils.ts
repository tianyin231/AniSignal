import type { ApiProduct, ApiProductSignal } from '~/composables/usePublicApi'

/** 履约信号的新变化优先；没有信号的商品放在后面。 */
export function sortProductsBySignal(items: ApiProduct[]) {
  const time = (product: ApiProduct) => Date.parse(product.fulfillment.updated_at || '') || 0
  return [...items].sort((left, right) => time(right) - time(left) || right.id - left.id)
}

/** 采纳方式与证据状态分开，自动结论不能标成人工确认。 */
export function productSignalReviewLabel(signal: Pick<ApiProductSignal, 'review_status' | 'review_method'>) {
  if (signal.review_status === 'verified') {
    return { ai: '自动采纳', rule: '官方来源', manual: '人工确认', pending: '已采纳' }[signal.review_method || 'pending']
  }
  return { candidate: '自动跟踪', rejected: '已驳回', conflict: '证据冲突' }[signal.review_status]
}
