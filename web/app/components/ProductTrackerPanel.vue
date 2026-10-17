<script setup lang="ts">
/**
 * 我的商品跟踪（批次 3b）：手动标记状态 + 到价提醒。
 * 只操作用户自己那几行数据；心理价位触发后按既有渠道（机器人/邮件/OnePush/Webhook）提醒。
 */
import { BellRing, PackageCheck, Trash2 } from 'lucide-vue-next'

interface TrackerAlert {
  id: number
  target_price: number
  enabled: boolean
  triggered_at: string | null
  triggered_price: number | null
  trigger_count: number
}

interface TrackerItem {
  product_id: number
  slug: string
  name: string
  cover_url: string | null
  state: string
  state_label: string
  note: string | null
  marked_at: string | null
  price_current: number | null
  price_observed_at: string | null
  alert: TrackerAlert | null
}

interface AlertHistoryItem {
  id: number
  product_id: number
  slug: string
  name: string
  target_price: number
  triggered_at: string | null
  triggered_price: number | null
  trigger_count: number
  price_current: number | null
  still_below: boolean
}

const items = ref<TrackerItem[]>([])
const stateOptions = ref<{ value: string, label: string }[]>([])
const history = ref<AlertHistoryItem[]>([])
const error = ref('')
const busy = ref(0)
const priceDraft = ref<Record<number, string>>({})

async function refresh() {
  error.value = ''
  try {
    const [tracker, alerts] = await Promise.all([
      $fetch<{ items: TrackerItem[], state_options: { value: string, label: string }[] }>('/api/account/product-tracker'),
      $fetch<{ items: AlertHistoryItem[] }>('/api/account/product-tracker/alerts/history'),
    ])
    items.value = tracker.items
    stateOptions.value = tracker.state_options
    history.value = alerts.items
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  }
}

onMounted(refresh)

function labelOf(value: string) {
  return stateOptions.value.find(option => option.value === value)?.label ?? value
}

async function markState(item: TrackerItem, state: string) {
  busy.value += 1
  try {
    await $fetch(`/api/account/product-tracker/${item.product_id}/state`, {
      method: 'PUT',
      body: { state, note: item.note ?? '' },
    })
    await refresh()
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    busy.value -= 1
  }
}

async function removeState(item: TrackerItem) {
  busy.value += 1
  try {
    await $fetch(`/api/account/product-tracker/${item.product_id}/state`, { method: 'DELETE' })
    await refresh()
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    busy.value -= 1
  }
}

async function saveAlert(item: TrackerItem) {
  const raw = (priceDraft.value[item.product_id] ?? '').trim()
  const price = Number(raw)
  if (!raw || !Number.isFinite(price) || price <= 0) {
    error.value = '心理价位要填大于 0 的数字'
    return
  }
  busy.value += 1
  error.value = ''
  try {
    await $fetch(`/api/account/product-tracker/${item.product_id}/price-alert`, {
      method: 'POST',
      body: { target_price: price },
    })
    delete priceDraft.value[item.product_id]
    await refresh()
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    busy.value -= 1
  }
}

async function toggleAlert(item: TrackerItem, enabled: boolean) {
  busy.value += 1
  try {
    await $fetch(`/api/account/product-tracker/${item.product_id}/price-alert`, {
      method: 'PATCH',
      body: { enabled },
    })
    await refresh()
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    busy.value -= 1
  }
}

async function removeAlert(item: TrackerItem) {
  busy.value += 1
  try {
    await $fetch(`/api/account/product-tracker/${item.product_id}/price-alert`, { method: 'DELETE' })
    await refresh()
  } catch (exc: any) {
    error.value = exc?.data?.detail ?? exc?.message ?? String(exc)
  } finally {
    busy.value -= 1
  }
}

function fmtTime(value: string | null) {
  return value ? value.slice(0, 16).replace('T', ' ') : '—'
}
</script>

<template>
  <section class="panel p-5">
    <h2 class="flex items-center gap-2 text-sm font-semibold">
      <PackageCheck :size="15" /> 我的商品跟踪
    </h2>
    <p class="mt-1 text-xs text-slate-500 dark:text-zinc-400">
      在商品页点「加入跟踪」后在这里标记状态、设心理价位；观测价低于心理价位时按既有渠道提醒一次。
    </p>
    <p v-if="error" class="mt-2 text-xs text-red-600 dark:text-red-400">{{ error }}</p>

    <ul class="mt-3 space-y-2">
      <li
        v-for="item in items"
        :key="item.product_id"
        class="rounded-md border border-slate-900/5 p-3 text-sm dark:border-white/10"
      >
        <div class="flex flex-wrap items-center gap-2">
          <NuxtLink :to="`/products/${item.slug}?view=dossier`" class="font-medium hover:underline">{{ item.name }}</NuxtLink>
          <span class="chip whitespace-nowrap bg-blue-500/10 text-blue-700 dark:text-blue-300">{{ item.state_label }}</span>
          <span v-if="item.price_current !== null" class="mono text-xs text-slate-400">
            观测价 ¥{{ item.price_current }}（{{ fmtTime(item.price_observed_at) }}）
          </span>
          <button
            class="ml-auto text-xs text-slate-500 hover:underline disabled:opacity-40 dark:text-zinc-400"
            :disabled="busy > 0"
            @click="removeState(item)"
          >
            <Trash2 :size="13" class="inline" /> 取消跟踪
          </button>
        </div>

        <div class="mt-2 flex flex-wrap items-center gap-1.5">
          <button
            v-for="option in stateOptions"
            :key="option.value"
            class="chip cursor-pointer whitespace-nowrap transition-opacity"
            :class="item.state === option.value
              ? 'bg-blue-500/15 text-blue-700 dark:text-blue-300'
              : 'bg-slate-500/10 text-slate-500 hover:opacity-80 dark:text-zinc-400'"
            :disabled="busy > 0"
            @click="markState(item, option.value)"
          >
            {{ option.label }}
          </button>
        </div>

        <div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <template v-if="item.alert">
            <span class="text-slate-500 dark:text-zinc-400">
              心理价位 <b class="mono">¥{{ item.alert.target_price }}</b>
            </span>
            <span v-if="item.alert.trigger_count" class="text-slate-400">
              已提醒 {{ item.alert.trigger_count }} 次，最近 {{ fmtTime(item.alert.triggered_at) }}
            </span>
            <button class="link" :disabled="busy > 0" @click="toggleAlert(item, !item.alert.enabled)">
              {{ item.alert.enabled ? '临时静音' : '重新启用' }}
            </button>
            <button class="link" :disabled="busy > 0" @click="removeAlert(item)">删除提醒</button>
          </template>
          <template v-else>
            <input
              v-model="priceDraft[item.product_id]"
              type="number"
              min="0"
              step="0.01"
              placeholder="心理价位"
              class="input w-28 px-2 py-1 text-xs"
              @keyup.enter="saveAlert(item)"
            >
            <button class="link" :disabled="busy > 0" @click="saveAlert(item)">
              <BellRing :size="12" class="inline" /> 设心理价位
            </button>
          </template>
        </div>
      </li>
      <li v-if="!items.length" class="text-xs text-slate-400 dark:text-zinc-500">
        还没有跟踪任何商品。打开任意商品页的「商品档案」页签即可加入。
      </li>
    </ul>

    <div v-if="history.length" class="mt-4 border-t border-slate-900/5 pt-3 dark:border-white/10">
      <h3 class="text-xs font-semibold text-slate-600 dark:text-zinc-300">到价提醒记录</h3>
      <ul class="mt-2 space-y-1 text-xs text-slate-500 dark:text-zinc-400">
        <li v-for="row in history" :key="row.id" class="flex flex-wrap items-center gap-2">
          <NuxtLink :to="`/products/${row.slug}?view=dossier`" class="hover:underline">{{ row.name }}</NuxtLink>
          <span class="mono">¥{{ row.triggered_price ?? row.price_current ?? '—' }}</span>
          <span class="mono">{{ fmtTime(row.triggered_at) }}</span>
          <span v-if="row.still_below" class="chip bg-emerald-500/10 text-emerald-700 dark:text-emerald-300">仍低于心理价位</span>
        </li>
      </ul>
    </div>
  </section>
</template>
