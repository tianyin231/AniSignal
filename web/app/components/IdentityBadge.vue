<script setup lang="ts">
import { identityMeta, type Identity } from '~/utils/data'

const props = defineProps<{ identity: Identity }>()
const meta = computed(() => identityMeta[props.identity])
</script>

<template>
  <span class="chip shrink-0 font-medium" :class="meta.badge">{{ meta.label }}</span>
</template>
