import type { ApiProduct } from '~/composables/usePublicApi'

/** 履约信号的新变化优先；没有信号的商品放在后面。 */
export function sortProductsBySignal(items: ApiProduct[]) {
  const time = (product: ApiProduct) => Date.parse(product.fulfillment.updated_at || '') || 0
  return [...items].sort((left, right) => time(right) - time(left) || right.id - left.id)
}
