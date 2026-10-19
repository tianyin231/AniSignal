<script setup lang="ts">
interface Policy {
  enabled: boolean; score_enabled: boolean
  first_party_threshold: number; media_threshold: number
  calls_per_minute: number; calls_per_hour: number; calls_per_day: number
  group_model: string; score_model: string
}
interface Source { id: number; name: string; platform: string; owner_key: string | null; editorial_tier: string | null; identity: string }
interface Receipt { id: number; task: string; ref_id: number; model: string; status: string; error: string | null }
const { api } = useAdminApi()
const toast = useAdminToast()
const policy = ref<Policy>()
const sources = ref<Source[]>([])
const receipts = ref<Receipt[]>([])
const busy = ref('')
const loadError = ref(false)
const numericFields = [
  { key: 'first_party_threshold', label: '一手来源精选门槛', max: 100 },
  { key: 'media_threshold', label: '媒体与个人精选门槛', max: 100 },
  { key: 'calls_per_minute', label: '归组/评分每分钟请求上限', max: 1000 },
  { key: 'calls_per_hour', label: '归组/评分每小时请求上限', max: 10000 },
  { key: 'calls_per_day', label: '归组/评分最近 24 小时请求上限', max: 100000 },
] as const
const labels: Record<string, string> = { received: '已保存', pending: '执行中', unknown: '结果待核对', invalid: '格式待修复', failed: '调用失败' }
async function load() {
  loadError.value = false
  try {
    const data = await api<{ policy: Policy; sources: Source[]; receipts: Receipt[] }>('/api/admin/ai/editorial')
    policy.value = data.policy; sources.value = data.sources; receipts.value = data.receipts
  } catch { loadError.value = true }
}
onMounted(load)
async function action(key: string, url: string, method: 'PUT' | 'POST' | 'PATCH', body?: Policy | Record<string, unknown>) {
  if (busy.value) return
  busy.value = key
  try {
    const result = await api<{ queued?: boolean }>(url, { method, body })
    toast.ok(key === 'run' ? result.queued ? '资讯编辑任务已入队' : '任务已在队列中' : '已保存')
    await load()
  } catch (error: any) { toast.err(error?.data?.detail || '操作失败') }
  finally { busy.value = '' }
}
function retry(receipt: Receipt) {
  if (!confirm('请先核对上游调用记录。重新请求可能再次计费，确认允许重试？')) return
  return action(`retry:${receipt.id}`, `/api/admin/ai/editorial/receipts/${receipt.id}/retry`, 'POST', { confirmed: true })
}
</script>

<template>
  <section class="panel p-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h2 class="text-sm font-semibold">AI 资讯编辑</h2>
      <button type="button" :disabled="!!busy" class="btn-primary min-h-11 px-4" @click="action('run', '/api/admin/ai/editorial/run', 'POST')">{{ busy === 'run' ? '提交中…' : '处理待归组与待评分资讯' }}</button>
    </div>
    <p v-if="loadError" role="alert" class="mt-3 text-sm text-warn">编辑配置加载失败。<button type="button" class="min-h-11 underline" @click="load">重新加载</button></p>
    <form v-if="policy" class="mt-4" @submit.prevent="action('policy', '/api/admin/ai/editorial', 'PUT', policy)">
      <div class="flex flex-wrap gap-5 text-sm">
        <label class="flex min-h-11 cursor-pointer items-center gap-2"><input v-model="policy.enabled" type="checkbox">自动归组与成刊</label>
        <label class="flex min-h-11 cursor-pointer items-center gap-2"><input v-model="policy.score_enabled" type="checkbox">独立双评分</label>
      </div>
      <div class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label v-for="field in numericFields" :key="field.key" class="block text-xs text-muted">{{ field.label }}
          <input v-model.number="policy[field.key]" type="number" min="0" :max="field.max" required class="input mono mt-1 min-h-11 w-full px-3">
        </label>
        <label class="block text-xs text-muted">归组模型（留空沿用接口配置）<input v-model="policy.group_model" maxlength="64" class="input mt-1 min-h-11 w-full px-3"></label>
        <label class="block text-xs text-muted">评分模型（留空沿用接口配置）<input v-model="policy.score_model" maxlength="64" class="input mt-1 min-h-11 w-full px-3"></label>
      </div>
      <button type="submit" :disabled="!!busy" class="btn-primary mt-4 min-h-11 px-4">{{ busy === 'policy' ? '保存中…' : '保存编辑规则' }}</button>
    </form>
    <details v-if="sources.length" class="mt-5 border-t border-line pt-2">
      <summary class="min-h-11 cursor-pointer content-center text-sm font-medium">来源归属与精选等级</summary>
      <p class="mb-3 text-xs leading-6 text-muted">同一机构的账号填写相同主体标识，计算热度时合并计数；留空则按独立账号计算。</p>
      <div class="space-y-3">
        <form v-for="source in sources" :key="source.id" class="grid items-end gap-2 rounded-lg bg-chip p-3 sm:grid-cols-[1fr_1fr_140px_auto]"
          @submit.prevent="action(`source:${source.id}`, `/api/admin/ai/editorial/sources/${source.id}`, 'PATCH', { owner_key: source.owner_key, editorial_tier: source.editorial_tier || null })">
          <span class="self-center text-sm">{{ source.name }} <span class="text-xs text-muted">{{ source.platform }}</span></span>
          <label class="text-xs text-muted">主体标识<input v-model="source.owner_key" maxlength="64" placeholder="如 openai" class="input mt-1 min-h-11 w-full px-3"></label>
          <label class="text-xs text-muted">精选等级<select v-model="source.editorial_tier" class="select mt-1 min-h-11 w-full px-2"><option :value="null">{{ source.identity === 'official' ? '自动：一手' : '自动：媒体/个人' }}</option><option value="first_party">一手来源</option><option value="media">媒体/个人</option></select></label>
          <button type="submit" :disabled="!!busy" class="min-h-11 rounded-lg border border-line px-4 text-sm">{{ busy === `source:${source.id}` ? '保存中…' : '保存' }}</button>
        </form>
      </div>
    </details>
    <details v-if="receipts.length" class="mt-4 border-t border-line pt-2">
      <summary class="min-h-11 cursor-pointer content-center text-sm font-medium">最近调用回执</summary>
      <ul class="divide-y divide-line">
        <li v-for="receipt in receipts" :key="receipt.id" class="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
          <div><span>{{ receipt.task === 'news_group' ? '事件归组' : '新闻评分' }} · #{{ receipt.ref_id }}</span><span class="ml-2 text-xs text-muted">{{ receipt.model }} · {{ labels[receipt.status] || receipt.status }}</span><p v-if="receipt.error" class="mt-1 text-xs text-warn">{{ receipt.error }}</p></div>
          <button v-if="receipt.status !== 'pending'" type="button" :disabled="!!busy" class="min-h-11 rounded-lg border border-line px-3 text-sm" @click="retry(receipt)">{{ busy === `retry:${receipt.id}` ? '处理中…' : '核对后允许重试' }}</button>
        </li>
      </ul>
    </details>
  </section>
</template>
