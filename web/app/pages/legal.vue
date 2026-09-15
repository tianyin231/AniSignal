<script setup lang="ts">
useSeoMeta({ title: '条款与隐私 · AniSignal' })

const docs = [
  {
    to: '/terms',
    title: '用户协议',
    desc: '服务范围、账号与使用规范、免责声明与终止条件。',
  },
  {
    to: '/privacy',
    title: '隐私政策',
    desc: '收集哪些数据、如何使用与保存、你的权利与联系方式。',
  },
  {
    to: '/copyright',
    title: '版权与下架',
    desc: '权利人投诉流程、站内内容下架与更正记录方式。',
  },
  {
    to: '/bot-rules',
    title: '机器人守则',
    desc: 'QQ / 邮件等推送通道的使用边界、频控与禁止行为。',
  },
  {
    to: '/about',
    title: '关于与产品边界',
    desc: '工作方式、不承诺的范围，以及与采集来源的关系。',
  },
  {
    to: '/sources',
    title: '来源健康',
    desc: '已接入平台、适配范围与运行状态的公开说明。',
  },
]
</script>

<template>
  <div class="space-y-4">
    <BackButton fallback="/" label="返回" />

    <section class="panel p-5 sm:p-6">
      <p class="mono text-xs text-blue-600 dark:text-blue-300">LEGAL</p>
      <h1 class="mt-1 text-lg font-semibold text-slate-900 dark:text-zinc-50">条款、隐私与合规</h1>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        AniSignal 为试运行中的信息采集与订阅工具。以下文书说明服务能做什么、不能做什么，以及你如何控制自己的数据。
        与页面展示冲突时，以本站实际功能与本页文书为准。
      </p>
    </section>

    <div class="grid gap-3 sm:grid-cols-2">
      <NuxtLink
        v-for="doc in docs"
        :key="doc.to"
        :to="doc.to"
        class="panel block p-5 transition hover:border-blue-300/80 dark:hover:border-blue-500/40"
      >
        <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">{{ doc.title }}</h2>
        <p class="mt-1.5 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ doc.desc }}</p>
        <span class="mt-3 inline-block text-xs text-blue-600 dark:text-blue-300">阅读 →</span>
      </NuxtLink>
    </div>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">数据边界一览</h2>
      <ul class="mt-2.5 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />站内内容以已配置采集目标与公开来源为准，不是全网镜像。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />原始链接指向源站；本站保存必要摘要与解析结果，用于检索、归并与订阅。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />账号邮箱、关注词、定制监控与投递记录仅用于提供服务，可申请删除。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />AI 摘要/翻译为辅助信息，可能出错；商品「待核验」不等于已确认事实。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />机器人推送依赖你或管理员配置的会话；添加进群本身不收费，增值服务另述。</li>
      </ul>
      <div class="mt-4 flex flex-wrap gap-4 text-sm">
        <NuxtLink to="/privacy" class="link">完整隐私政策</NuxtLink>
        <NuxtLink to="/copyright" class="link">版权投诉</NuxtLink>
        <NuxtLink to="/bot-rules" class="link">机器人守则</NuxtLink>
      </div>
    </section>
  </div>
</template>
