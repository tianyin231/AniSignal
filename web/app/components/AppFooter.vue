<script setup lang="ts">
const { t } = useSiteConfig()
// 原左侧栏的工具页（数据统计 / 来源健康 / 订阅中心）收进页脚第一组
const tools = [
  { to: '/stats', label: '数据统计' },
  { to: '/sources', label: '来源健康' },
  { to: '/watch', label: '订阅中心' },
]
const about = [
  { to: '/intro', label: '产品介绍' },
  { to: '/about', label: '关于' },
  { to: '/legal', label: '条款与隐私' },
  { to: '/bot-rules', label: '机器人守则' },
]
</script>

<template>
  <footer class="mt-8 border-t border-line bg-card/80">
    <div class="mx-auto flex max-w-[1280px] flex-col gap-4 px-4 py-6 text-[13px] text-muted lg:flex-row lg:items-center lg:justify-between lg:px-8">
      <div class="flex items-center gap-3">
        <span class="num text-[15px] font-bold text-ink-2">AniSignal</span>
        <span class="text-faint">{{ t('footer_note') }}</span>
      </div>
      <nav class="flex flex-wrap gap-x-5 gap-y-2" aria-label="页脚">
        <NuxtLink v-for="l in tools" :key="l.to" :to="l.to" :prefetch-on="l.to === '/stats' ? 'interaction' : 'visibility'" class="transition-colors hover:text-ink">{{ l.label }}</NuxtLink>
        <span class="hidden h-4 w-px self-center bg-line sm:block" aria-hidden="true" />
        <NuxtLink v-for="l in about" :key="l.to" :to="l.to" class="transition-colors hover:text-ink">{{ l.label }}</NuxtLink>
        <a href="/rss.xml" class="transition-colors hover:text-ink">RSS</a>
      </nav>
    </div>
  </footer>
</template>
