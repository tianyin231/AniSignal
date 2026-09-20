<script setup lang="ts">
import { Bell, Mail, Radar, Rss } from 'lucide-vue-next'
import { GAME_COMMUNITIES } from '~/composables/useGameSection'

useSeoMeta({ title: '订阅中心' })
const community = ref('all')
const choices = [
  { title: '关注关键词', description: '从本站资讯中筛出你关心的角色、商品和话题。', to: '/account#keyword-monitor', action: '设置关注', icon: Bell },
  { title: '邮件通知', description: '接收每日早报，也可以根据关注词生成精炼通知。', to: '/account#email-notifications', action: '设置邮件通知', icon: Mail },
  { title: '定制监控', description: '跟踪 B站账号或关键词。监控可以仅自己可见。', to: '/account#custom-monitors', action: '创建监控', icon: Radar },
]
</script>

<template>
  <div class="space-y-4">
    <h1 class="px-1 text-lg font-semibold text-slate-900 dark:text-zinc-50">订阅中心</h1>
    <section v-for="choice in choices" :key="choice.title" class="panel flex flex-wrap items-center gap-4 p-4 sm:p-5">
      <component :is="choice.icon" :size="22" class="shrink-0 text-blue-600 dark:text-blue-300" aria-hidden="true" />
      <div class="min-w-0 flex-1 basis-48">
        <h2 class="font-semibold text-slate-900 dark:text-zinc-100">{{ choice.title }}</h2>
        <p class="mt-1 text-sm leading-relaxed text-slate-600 dark:text-zinc-400">{{ choice.description }}</p>
      </div>
      <NuxtLink :to="choice.to" class="btn-primary px-3 py-2 text-sm">{{ choice.action }}</NuxtLink>
    </section>
    <section class="panel p-4 sm:p-5">
      <div class="flex items-center gap-3">
        <Rss :size="22" class="text-blue-600 dark:text-blue-300" aria-hidden="true" />
        <h2 class="font-semibold text-slate-900 dark:text-zinc-100">RSS 订阅</h2>
      </div>
      <div class="mt-3 flex flex-wrap gap-2">
        <select v-model="community" aria-label="RSS 游戏范围" class="select px-3 py-2 text-sm">
          <option value="all">全部游戏</option>
          <option v-for="c in GAME_COMMUNITIES" :key="c.key" :value="c.key">{{ c.label }}</option>
        </select>
        <a :href="`/rss.xml?community=${community}`" class="btn-primary px-3 py-2 text-sm">打开订阅源</a>
      </div>
    </section>
  </div>
</template>
