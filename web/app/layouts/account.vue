<script setup lang="ts">
import AppShell from '~/components/AppShell.vue'
</script>

<template>
  <AppShell>
    <main class="min-w-0 py-6 lg:py-8">
      <div class="wall-heading mb-5 flex items-center justify-between gap-3">
        <h1 class="text-2xl font-bold text-ink">账号中心</h1>
        <NuxtLink to="/" class="text-sm text-muted transition-colors hover:text-ink">返回站点</NuxtLink>
      </div>
      <slot />
    </main>
  </AppShell>
</template>
