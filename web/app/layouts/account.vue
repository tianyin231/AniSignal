<script setup lang="ts">
import AppShell from '~/components/AppShell.vue'
</script>

<template>
  <AppShell>
    <main class="min-w-0 py-6 lg:py-8">
      <slot />
    </main>
  </AppShell>
</template>
