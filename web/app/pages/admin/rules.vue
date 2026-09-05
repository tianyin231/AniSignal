<script setup lang="ts">
import { ADMIN_COMMUNITIES } from '~/utils/admin-communities'

definePageMeta({ layout: 'admin', middleware: 'admin-auth' })
useSeoMeta({ title: '分类词典 · AniSignal 管理看板' })

interface Rule {
  id: number
  category: string
  pattern: string
  platform: string | null
  priority: number
  enabled: boolean
}

const { api } = useAdminApi()
const rules = ref<Rule[]>([])
const error = ref<string | null>(null)
const busy = ref(false)
const toast = useAdminToast()

const categories = [
  'presale', 'balance', 'delay', 'unboxing', 'quality', 'review',
  'arrived', 'shipping', 'version_update', 'official_announce', 'promotion', 'other',
]
const categoryLabels: Record<string, string> = {
  presale: '预售', balance: '尾款', delay: '延期', unboxing: '开箱', quality: '质量',
  review: '评测', arrived: '到货', shipping: '发货', version_update: '版本',
  official_announce: '官方公告', promotion: '推广', other: '其他',
}

const form = reactive({ category: 'unboxing', pattern: '', priority: 100, community: '' })

async function refresh() {
  try {
    rules.value = await api<Rule[]>('/api/admin/classification-rules')
  } catch (exc: any) {
    error.value = exc?.message ?? String(exc)
  }
}
onMounted(refresh)

async function create() {
  if (!form.pattern.trim()) return
  busy.value = true
  try {
    await api('/api/admin/classification-rules', { method: 'POST', body: { ...form, community: form.community || null, pattern: form.pattern.trim() } })
    toast.ok('规则已新增；点击「全量重算」生效')
    form.pattern = ''
    await refresh()
  } catch (exc: any) {
    error.value = `创建失败：${exc?.data?.detail ?? exc?.message}`
  } finally {
    busy.value = false
  }
}

async function toggle(rule: Rule) {
  busy.value = true
  try {
    await api(`/api/admin/classification-rules/${rule.id}`, {
      method: 'PUT',
      body: { category: rule.category, pattern: rule.pattern, platform: rule.platform, priority: rule.priority, enabled: !rule.enabled },
    })
    toast.ok(rule.enabled ? '已停用' : '已启用')
    await refresh()
  } finally {
    busy.value = false
  }
}

async function remove(rule: Rule) {
  busy.value = true
  try {
    await api(`/api/admin/classification-rules/${rule.id}`, { method: 'DELETE' })
    toast.ok('规则已删除')
    await refresh()
  } finally {
    busy.value = false
  }
}

async function reparseAll() {
  if (!confirm('用当前词典与解析器重算全部内容与事件？（原始数据不变，可能产生新版本与事件）')) return
  busy.value = true
  try {
    toast.info('全量重算执行中（可能需要数十秒）…')
    const result = await api('/api/admin/reparse', { method: 'POST' })
    toast.ok(`重算完成：检查 ${result.checked} 条，更新 ${result.updated} 条`)
    await refresh()
  } catch (exc: any) {
    error.value = `重算失败：${exc?.message}`
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-lg font-semibold">分类词典</h1>
        <p class="mt-0.5 text-sm text-slate-500 dark:text-zinc-400">
          标题/正文命中的关键词 → 类目；数字越小优先级越高。修改后需「全量重算」生效。
        </p>
      </div>
      <button class="btn-primary px-4 py-2" :disabled="busy" @click="reparseAll">{{ busy ? '重算中…' : '全量重算' }}</button>
    </div>

    <p v-if="error" class="panel px-4 py-3 text-sm text-red-600 dark:text-red-400">{{ error }}</p>

    <form class="panel flex flex-wrap gap-3 p-4" @submit.prevent="create">
      <select v-model="form.category" class="select px-3 py-2 text-sm">
        <option v-for="c in categories" :key="c" :value="c">{{ categoryLabels[c] ?? c }}</option>
      </select>
      <input v-model="form.pattern" placeholder="关键词，如 翻车" class="input flex-1 px-3 py-2 text-sm">
      <select v-model="form.community" class="select px-3 py-2 text-sm" title="通用规则对全部分区生效；限定后只作用于该分区">
        <option value="">全部游戏</option>
        <option v-for="c in ADMIN_COMMUNITIES" :key="c.key" :value="c.key">仅{{ c.label }}</option>
      </select>
      <input v-model.number="form.priority" type="number" min="0" max="1000" class="input mono w-24 px-3 py-2 text-sm">
      <button type="submit" class="btn-primary px-4 py-2" :disabled="busy || !form.pattern.trim()">新增</button>
    </form>

    <div class="panel overflow-x-auto">
      <table class="w-full min-w-xl text-sm">
        <thead>
          <tr class="border-b border-slate-900/5 text-left text-xs text-slate-500 dark:border-white/10 dark:text-zinc-400">
            <th class="px-4 py-2.5 font-medium">优先级</th>
            <th class="px-3 py-2.5 font-medium">关键词</th>
            <th class="px-3 py-2.5 font-medium">类目</th>
            <th class="px-3 py-2.5 font-medium">平台</th>
            <th class="px-3 py-2.5 font-medium">状态</th>
            <th class="px-4 py-2.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in rules" :key="rule.id" class="border-b border-slate-900/5 last:border-0 dark:border-white/5" :class="{ 'opacity-40': !rule.enabled }">
            <td class="mono px-4 py-2.5">{{ rule.priority }}</td>
            <td class="px-3 py-2.5 font-medium">{{ rule.pattern }}</td>
            <td class="px-3 py-2.5">{{ categoryLabels[rule.category] ?? rule.category }}</td>
            <td class="px-3 py-2.5 text-slate-500 dark:text-zinc-400">{{ rule.platform ?? '全部' }}</td>
            <td class="px-3 py-2.5">
              <span class="chip" :class="rule.enabled ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : 'bg-slate-500/10 text-slate-400'">
                {{ rule.enabled ? '启用' : '停用' }}
              </span>
            </td>
            <td class="whitespace-nowrap px-4 py-2.5 text-right">
              <button class="rounded px-2 py-1 text-xs text-slate-500 hover:underline dark:text-zinc-400" :disabled="busy" @click="toggle(rule)">{{ rule.enabled ? '停用' : '启用' }}</button>
              <button class="rounded px-2 py-1 text-xs text-red-500 hover:underline dark:text-red-400" :disabled="busy" @click="remove(rule)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
