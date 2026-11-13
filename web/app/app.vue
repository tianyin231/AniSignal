<script setup lang="ts">
import type { PublicSiteConfig } from '~/composables/useSiteConfig'
import { buildDynamicColorCss } from '~/utils/community-palette'

const { api } = usePublicApi()
const { data: siteConfig } = await useAsyncData('site-config', (_app, { signal }) => api<PublicSiteConfig>('/api/site-config', { signal }))
const dynamicPaletteCss = computed(() =>
  buildDynamicColorCss((siteConfig.value?.all_communities ?? []).map(c => c.color)),
)

const route = useRoute()
const requestUrl = useRequestURL()
const { siteUrl } = useRuntimeConfig().public
const baseUrl = computed(() => (siteUrl || requestUrl.origin).replace(/\/+$/, ''))
// 规范地址保留分区；页码、筛选和搜索词都归并到同一页
const canonical = computed(() => {
  const community = route.query.community
  return `${baseUrl.value}${route.path}${typeof community === 'string' && community ? `?community=${encodeURIComponent(community)}` : ''}`
})
useHead({
  titleTemplate: title => !title ? 'AniSignal · 让重要的变化，找到你' : title.includes('AniSignal') ? title : `${title} · AniSignal`,
  link: [{ rel: 'canonical', href: canonical }],
  style: [{ key: 'community-palette', innerHTML: dynamicPaletteCss }],
})
useSeoMeta({
  ogSiteName: 'AniSignal',
  ogType: 'website',
  ogUrl: canonical,
  ogImage: () => `${baseUrl.value}/og.jpg`,
  ogImageWidth: 1200,
  ogImageHeight: 630,
  twitterCard: 'summary_large_image',
})
</script>

<template>
  <NuxtLoadingIndicator color="#3b82f6" :height="2" />
  <!-- 壁纸与材质状态属于应用，切换布局时不能卸载重建。 -->
  <WallpaperBackdrop />
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>
