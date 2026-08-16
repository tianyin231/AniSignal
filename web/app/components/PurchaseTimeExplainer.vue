<script setup lang="ts">
const options = [
  { key: 'none', label: '尚未购买' },
  { key: 'day1', label: '首发当天' },
  { key: 'days', label: '首发后几天' },
  { key: 'late', label: '更晚补购' },
]
const explainers: Record<string, string> = {
  none: '当前处于「多名独立用户到货」阶段，到货样本均为首发订单。若你现在补购，队列更靠后，公开样本的参考价值最低，建议等待「普遍到货」信号出现后再决策。',
  day1: '现有 7 个独立到货样本的订单集中在首发当天下单，与你的购买时间接近，参考程度较高。下一档信号是「相近购买时间样本到货」，出现时系统会即时通知你。',
  days: '已到货样本以首发当天订单为主，你的订单队列更靠后。履约方向是正向的（已从「首位到货」推进到「多名到货」），但尚不能据此推断你的发货时间。',
  late: '当前全部到货样本的购买时间都早于你的订单，只能证明履约已经开始，不能证明你的订单临近发货。建议关注「相近购买时间样本到货」这一档信号。',
}
const selected = ref('day1')
</script>

<template>
  <section class="panel p-4">
    <div class="flex items-center justify-between gap-3">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">购买时间解释器</h2>
      <span class="text-xs text-slate-400">免费 · 无需登录</span>
    </div>
    <div class="mt-3 inline-flex max-w-full flex-wrap gap-0.5 rounded-lg bg-slate-200/60 p-0.5 dark:bg-zinc-800/70">
      <button
        v-for="o in options"
        :key="o.key"
        type="button"
        class="rounded-md px-2.5 py-1 text-xs transition-all"
        :class="
          selected === o.key
            ? 'bg-white font-medium text-blue-700 shadow-sm dark:bg-zinc-700 dark:text-blue-300'
            : 'text-slate-500 hover:text-slate-700 dark:text-zinc-400 dark:hover:text-zinc-200'
        "
        @click="selected = o.key"
      >
        {{ o.label }}
      </button>
    </div>
    <p class="mt-3 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">{{ explainers[selected] }}</p>
    <p class="mt-3 border-t border-slate-100 pt-2.5 text-xs leading-relaxed text-slate-400 dark:border-zinc-800 dark:text-zinc-500">
      以上为基于公开样本的阶段解释，不构成发货日期预测；样本仅来自已接入来源。
    </p>
  </section>
</template>
