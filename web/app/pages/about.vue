<script setup lang="ts">
useSeoMeta({ title: '关于' })

const steps = [
  { n: '01', t: '采集', d: '只接入可公开访问的官方账号与公开内容；每个来源的健康状态公开可查。' },
  { n: '02', t: '合并', d: '同一事实的多平台公告归并为一个事件；转载不重复计数。' },
  { n: '03', t: '证据', d: '每项结论保留原始链接、来源身份与冲突证据；结论可以修正，修正历史留痕。' },
  { n: '04', t: '送达', d: '重要变化即时邮件，普通更新进入次日 08:00 早报；每封邮件说明触发原因。' },
]
const boundaries = [
  '不承诺全网、实时或永久可用；「未发现」始终指「在已接入来源中未发现」。',
  '不根据公开样本预测具体订单的发货日期；购买时间只用于解释样本参考程度。',
  '送测内容与普通消费者订单严格区分，不做软广认定。',
  '提供权利人投诉、更正与退订入口；原内容删除或修改后如实记录状态。',
]
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-5 sm:p-6">
      <h1 class="text-lg font-semibold text-slate-900 dark:text-zinc-50">关于 AniSignal</h1>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        AniSignal 不是资讯聚合器，也不是新的社区。它把分散在多个平台的信息归并为可追溯的「事件」，
        帮你减少跨平台搜集、翻译、去重和判断信息的成本——订阅一个关注目标后，不再每天蹲评论区。
      </p>
      <p class="mt-2 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
        当前覆盖《鸣潮》《碧蓝航线》《崩坏：星穹铁道》分区（持续扩展更多游戏）：官方情报、社区线索与商品履约追踪按分区独立组织，账号与订阅全分区通用。全站事件与时间线免费浏览，订阅提供邮件送达。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">工作方式</h2>
      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <div v-for="s in steps" :key="s.n" class="rounded-[10px] border border-slate-200/70 p-3 dark:border-zinc-800">
          <p class="mono text-xs text-blue-600 dark:text-blue-300">{{ s.n }}</p>
          <p class="mt-0.5 text-sm font-medium text-slate-800 dark:text-zinc-200">{{ s.t }}</p>
          <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ s.d }}</p>
        </div>
      </div>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">边界与承诺</h2>
      <ul class="mt-2.5 space-y-2">
        <li v-for="b in boundaries" :key="b" class="flex gap-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
          <span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />
          {{ b }}
        </li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">相关入口</h2>
      <div class="mt-2.5 flex flex-wrap gap-4 text-sm">
        <NuxtLink to="/sources" class="link">已接入来源与健康状态</NuxtLink>
        <NuxtLink to="/watch" class="link">订阅中心</NuxtLink>
        <a href="mailto:admin@anisignal.local" class="link">权利人投诉</a>
      </div>
    </section>
  </div>
</template>
