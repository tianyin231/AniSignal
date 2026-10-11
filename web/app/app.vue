<script setup lang="ts">
import type { PublicSiteConfig } from '~/composables/useSiteConfig'

const { api } = usePublicApi()
useAsyncData('site-config', (_app, { signal }) => api<PublicSiteConfig>('/api/site-config', { signal }))
</script>

<template>
  <NuxtLoadingIndicator color="#3b82f6" :height="2" />
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>
