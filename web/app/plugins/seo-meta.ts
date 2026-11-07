import { InferSeoMetaPlugin } from '@unhead/vue/plugins'

/** 分享卡片标题和摘要跟随页面的 title / description，各页只维护一份。 */
export default defineNuxtPlugin(() => {
  injectHead().use(InferSeoMetaPlugin())
})
