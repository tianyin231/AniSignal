<script setup lang="ts">
interface Row { id: string; title: string; source: string; processing_state: string; visibility: string | null; selected: boolean | null }
interface Chain { article: { id: string; title: string; processing_state: string; processing_error: string | null }; publication: { title: string; summary: string; visibility: string } | null; override: { version: number } | null; analyses: { id: number; score: number; relevance: string; receipts: { id: number; status: string }[] }[] }
const { api } = useAdminApi()
const toast = useAdminToast()
const query = ref('')
const rows = ref<Row[]>([])
const chain = ref<Chain | null>(null)
const reason = ref('')
const busy = ref(false)
async function search() {
  try { rows.value = (await api<{ rows: Row[] }>('/api/admin/aihot/content', { query: { q: query.value } })).rows }
  catch { toast.err('内容加载失败') }
}
async function open(id: string) {
  chain.value = await api<Chain>(`/api/admin/aihot/content/${id}`)
}
async function visibility(hidden: boolean) {
  if (!chain.value || !reason.value.trim()) return
  busy.value = true
  try {
    await api(`/api/admin/aihot/content/${chain.value.article.id}/visibility`, { method: 'POST', body: {
      visibility: hidden ? 'withdrawn' : 'public', reason: reason.value, version: chain.value.override?.version || 0,
    } })
    await open(chain.value.article.id); await search(); toast.ok('发布状态已更新')
  } catch { toast.err('状态已变化，请重新打开内容后再试') }
  finally { busy.value = false }
}
async function rerun(step: string) {
  if (!chain.value) return
  busy.value = true
  try { await api(`/api/admin/aihot/content/${chain.value.article.id}/rerun`, { method: 'POST', body: { step, requestId: crypto.randomUUID() } }); toast.ok('任务已入队') }
  catch { toast.err('任务提交失败') }
  finally { busy.value = false }
}
onMounted(search)
</script>
<template>
  <section class="panel p-5">
    <h2 class="font-semibold text-ink">内容与发布核对</h2>
    <form class="mt-3 flex gap-3" @submit.prevent="search"><input v-model="query" placeholder="标题、原文地址或内容 ID" aria-label="搜索引擎内容" class="min-w-0 flex-1 rounded-lg border border-line bg-card p-2 text-sm text-ink"><button class="rounded-lg border border-line px-4 py-2 text-sm text-ink">查询</button></form>
    <ul class="mt-4 max-h-96 divide-y divide-line overflow-y-auto"><li v-for="row in rows" :key="row.id" class="py-3"><button class="text-left text-sm text-ink hover:text-brand-ink" @click="open(row.id)">{{ row.title }}</button><p class="mt-1 text-xs text-muted">{{ row.source }} · {{ row.processing_state }} · {{ row.visibility || '尚未发布' }}{{ row.selected ? ' · 精选' : '' }}</p></li></ul>
    <div v-if="chain" class="mt-5 rounded-xl border border-line p-4"><div class="flex justify-between gap-3"><h3 class="font-semibold text-ink">{{ chain.publication?.title || chain.article.title }}</h3><button aria-label="关闭内容核对" class="text-muted" @click="chain = null">关闭</button></div><p class="mt-2 text-xs text-muted">{{ chain.article.id }} · {{ chain.article.processing_state }} · {{ chain.publication?.visibility || '尚未发布' }}</p><p v-if="chain.article.processing_error" class="mt-2 break-all text-xs text-warn">{{ chain.article.processing_error }}</p><p class="mt-3 text-sm leading-7 text-ink-2">{{ chain.publication?.summary }}</p><p v-for="analysis in chain.analyses" :key="analysis.id" class="mt-2 text-xs text-muted">判定 {{ analysis.relevance }} · 分数 {{ analysis.score }} · 回执 <span v-for="receipt in analysis.receipts" :key="receipt.id" class="mr-2">#{{ receipt.id }} {{ receipt.status }}</span></p><label class="mt-4 block text-xs text-ink-2">发布状态调整原因<input v-model="reason" class="mt-1 block w-full rounded-lg border border-line bg-card p-2 text-sm"></label><div class="mt-3 flex flex-wrap gap-4 text-xs"><button :disabled="busy || !reason.trim()" class="text-warn disabled:opacity-40" @click="visibility(true)">撤稿</button><button :disabled="busy || !reason.trim()" class="text-brand-ink disabled:opacity-40" @click="visibility(false)">恢复公开</button><button :disabled="busy" class="text-brand-ink" @click="rerun('analyze')">重新评判（占调用预算）</button><button :disabled="busy" class="text-brand-ink" @click="rerun('group')">重新归组</button><NuxtLink :to="`/ai/items/${chain.article.id}`" class="text-brand-ink">查看公开页 →</NuxtLink></div></div>
  </section>
</template>
