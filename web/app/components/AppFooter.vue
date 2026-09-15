<script setup lang="ts">
const { t } = useSiteConfig()
</script>

<template>
  <footer class="mt-6 border-t border-slate-200/80 dark:border-zinc-800">
    <div
      class="mx-auto flex max-w-[1280px] flex-col gap-2 px-4 py-6 text-xs text-slate-400 dark:text-zinc-500 sm:flex-row sm:items-center sm:justify-between lg:px-6"
    >
      <p>{{ t('footer_note') }}</p>
      <nav class="flex flex-wrap gap-x-4 gap-y-1">
        <NuxtLink to="/intro" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300">产品介绍</NuxtLink>
        <NuxtLink to="/about" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300">关于</NuxtLink>
        <NuxtLink to="/sources" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300">来源与边界</NuxtLink>
        <NuxtLink to="/legal" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300">条款与隐私</NuxtLink>
        <NuxtLink to="/bot-rules" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300">机器人守则</NuxtLink>
        <a href="/rss.xml" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300">RSS 订阅</a>
      </nav>
    </div>
  </footer>
</template>
