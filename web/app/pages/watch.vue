<script setup lang="ts">
// 订阅功能尚未上线：诚实说明规划，不用演示数据冒充
useSeoMeta({ title: '订阅中心' })

const plans = [
  {
    name: '免费浏览',
    price: '￥0',
    now: true,
    items: [
      '全站事件、时间线与原始内容免费查看',
      '每项结论附原始来源链接',
      '来源健康状态公开可查',
    ],
  },
  {
    name: '基础订阅（规划中）',
    price: '￥3 / 30 天',
    items: [
      '每天 08:00 官方早报（邮件）',
      '一个个性化监控位：商品 / 账号 / 主题',
      '重要状态变化即时邮件，附触发原因',
    ],
  },
]
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-4 sm:p-5">
      <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">订阅中心</h1>
      <p class="mt-1.5 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        邮件通知、早报与监控位正在开发中。上线前，本站全部信息免费开放，无需注册；
        你可以随时查看事件库与商品时间线获取同样的信息。
      </p>
    </section>

    <div class="grid gap-4 sm:grid-cols-2">
      <section
        v-for="plan in plans"
        :key="plan.name"
        class="panel p-4 sm:p-5"
        :class="{ 'ring-1 ring-blue-500/30': plan.now }"
      >
        <div class="flex items-baseline justify-between gap-2">
          <h2 class="font-semibold text-slate-900 dark:text-zinc-50">{{ plan.name }}</h2>
          <span v-if="plan.now" class="chip bg-blue-500/10 text-blue-700 dark:text-blue-300">当前可用</span>
        </div>
        <p class="mono mt-1 text-sm text-slate-500 dark:text-zinc-400">{{ plan.price }}</p>
        <ul class="mt-3 space-y-1.5 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
          <li v-for="item in plan.items" :key="item" class="flex gap-2">
            <span class="mt-2 size-1 shrink-0 rounded-full bg-blue-400" />
            {{ item }}
          </li>
        </ul>
      </section>
    </div>

    <section class="panel p-4 sm:p-5">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">通知纪律</h2>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        同一事件、同一状态只通知一次；新内容未改变结论时不单独打扰；无新增的早报会明确说明而不是静默跳过；
        每封邮件说明为什么收到、发生了什么，并提供退订入口。
      </p>
    </section>
  </div>
</template>
