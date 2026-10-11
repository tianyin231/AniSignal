<template>
  <div class="space-y-4">
    <StatusCard v-if="!isCustom" />
    <SourceHealthMini />
    <SubscribeCta />
  </div>
</template>

<script setup lang="ts">
const route = useRoute()
const isCustom = computed(() => route.path === '/custom' || route.path.startsWith('/custom/'))
</script>
