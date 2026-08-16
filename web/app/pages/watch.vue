<script setup lang="ts">
const types = [
  { key: 'product', label: '商品监控', desc: '预售、尾款、延期、发货、到货、质量与售后' },
  { key: 'account', label: '账号监控', desc: '指定公开账号的新动态、视频或作品' },
  { key: 'topic', label: '主题监控', desc: '预设主题热度首次越过你设置的阈值' },
]
const route = useRoute()
const initialType = () => {
  const t = String(route.query.type || '')
  return ['product', 'account', 'topic'].includes(t) ? t : 'product'
}
const activeType = ref(initialType())
const saved = ref(false)
const purchase = ref('day1')
const email = ref('')
const instant = ref(true)
const digest = ref(true)

const purchaseOptions = [
  { key: 'none', label: '尚未购买' },
  { key: 'day1', label: '首发当天' },
  { key: 'days', label: '首发后几天' },
  { key: 'late', label: '更晚补购' },
]

useSeoMeta({ title: '订阅中心' })
</script>

<template>
  <div class="space-y-4">
    <div class="panel border-dashed p-4 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
      原型未接入登录与支付（规划：邮箱验证码登录，无密码）。以下为演示数据与交互演示。
    </div>

    <WatchOverview />

    <section class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">新建监控位</h2>
      <div class="mt-3 grid gap-2 sm:grid-cols-3">
        <button
          v-for="t in types"
          :key="t.key"
          type="button"
          class="rounded-md border p-3 text-left transition-colors"
          :class="
            activeType === t.key
              ? 'border-blue-500/60 bg-blue-500/5'
              : 'border-slate-200 hover:border-slate-300 dark:border-zinc-700 dark:hover:border-zinc-600'
          "
          @click="activeType = t.key"
        >
          <p class="text-sm font-medium" :class="activeType === t.key ? 'text-blue-600 dark:text-blue-300' : 'text-slate-700 dark:text-zinc-200'">
            {{ t.label }}
          </p>
          <p class="mt-1 text-xs leading-relaxed text-slate-400 dark:text-zinc-500">{{ t.desc }}</p>
        </button>
      </div>

      <div class="mt-5 space-y-4">
        <div v-if="activeType === 'product'">
          <p class="text-xs font-medium text-slate-500 dark:text-zinc-400">大致购买时间（不要求订单号）</p>
          <div class="mt-2 flex flex-wrap gap-1.5">
            <button
              v-for="o in purchaseOptions"
              :key="o.key"
              type="button"
              class="rounded-md border px-2.5 py-1 text-xs transition-colors"
              :class="
                purchase === o.key
                  ? 'border-blue-500/60 bg-blue-500/10 font-medium text-blue-600 dark:text-blue-300'
                  : 'border-slate-200 text-slate-500 hover:border-slate-300 dark:border-zinc-700 dark:text-zinc-400'
              "
              @click="purchase = o.key"
            >
              {{ o.label }}
            </button>
          </div>
        </div>
        <div v-else-if="activeType === 'account'">
          <p class="text-xs font-medium text-slate-500 dark:text-zinc-400">账号</p>
          <p class="mt-2 rounded-md border border-slate-200 px-3 py-2 text-xs text-slate-400 dark:border-zinc-700 dark:text-zinc-500">
            演示：系统确认平台与唯一身份后，由你确认目标，不把搜索第一项当成目标。
          </p>
        </div>
        <div v-else>
          <p class="text-xs font-medium text-slate-500 dark:text-zinc-400">主题与阈值</p>
          <p class="mt-2 rounded-md border border-slate-200 px-3 py-2 text-xs text-slate-400 dark:border-zinc-700 dark:text-zinc-500">
            演示：主题来自系统预设词典；阈值按平台与指标分别设置（如 B站播放量 ≥ 500,000），首次越限通知一次。
          </p>
        </div>

        <div>
          <label class="text-xs font-medium text-slate-500 dark:text-zinc-400" for="watch-email">接收邮箱</label>
          <input
            id="watch-email"
            v-model="email"
            type="email"
            placeholder="you@example.com"
            class="mt-2 w-full max-w-sm rounded-md border border-slate-200 bg-white px-3 py-2 text-sm outline-none transition-colors placeholder:text-slate-300 focus:border-blue-400 dark:border-zinc-700 dark:bg-zinc-900 dark:placeholder:text-zinc-600"
          />
        </div>

        <div class="flex flex-wrap gap-4 text-sm">
          <label class="flex items-center gap-2 text-slate-600 dark:text-zinc-300">
            <input v-model="instant" type="checkbox" class="accent-blue-600" />
            重要状态变化即时邮件
          </label>
          <label class="flex items-center gap-2 text-slate-600 dark:text-zinc-300">
            <input v-model="digest" type="checkbox" class="accent-blue-600" />
            每日 08:00 早报
          </label>
        </div>

        <div v-if="!saved">
          <button type="button" class="btn-primary px-4 py-2" @click="saved = true">
            保存监控位
          </button>
          <span class="ml-3 text-xs text-slate-400 dark:text-zinc-500">一个监控位 = 一个目标 + 一种主要触发条件</span>
        </div>
        <div v-else class="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2.5 text-sm text-emerald-700 dark:text-emerald-300">
          已创建监控位（原型演示，未接入后端）。
          <button type="button" class="link ml-1" @click="saved = false">再建一个</button>
        </div>
      </div>
    </section>

    <section class="panel p-4">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">通知纪律</h2>
      <ul class="mt-2 space-y-1.5 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        <li>· 同一事件、同一状态、同一用户只发送一次；跨平台重复公告合并。</li>
        <li>· 新内容未改变事件结论时，不单独即时发送，进入次日早报。</li>
        <li>· 每封邮件说明为什么收到、发生了什么、与你的关系，并附原始链接与退订入口。</li>
      </ul>
    </section>
  </div>
</template>
