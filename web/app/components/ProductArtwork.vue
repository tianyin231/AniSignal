<script setup lang="ts">
import { imageSrcset, imageUrl } from '~/utils/image'

const props = defineProps<{ src?: string | null; name: string; kind?: string | null; priority?: boolean; thumbnail?: boolean; cover?: boolean }>()
const failed = ref(false)
watch(() => props.src, () => { failed.value = false })
</script>

<template>
  <div class="relative isolate overflow-hidden bg-chip">
    <!-- cover 供列表封面位：竖构图手办图 contain 会缩成中间一条，
         裁切填充并把焦点放在中上部（商品主体常在的位置）；
         详情画廊与缩略图保持 contain 看全图 -->
    <img
      v-if="src && !failed"
      :src="imageUrl(src, 960)"
      :srcset="imageSrcset(src, [480, 960, 1440])"
      sizes="(min-width: 1024px) 420px, (min-width: 640px) 50vw, 100vw"
      :alt="`${name}官方商品图`"
      :loading="priority ? 'eager' : 'lazy'"
      :fetchpriority="priority ? 'high' : 'auto'"
      decoding="async"
      referrerpolicy="no-referrer"
      class="absolute inset-0 size-full"
      :class="cover
        ? 'object-cover object-[center_30%]'
        : ['object-contain', thumbnail ? 'p-0.5' : 'p-3 sm:p-5']"
      @error="failed = true"
    >
    <div v-else class="absolute inset-0 flex flex-col items-center justify-center gap-3 text-faint" :aria-label="`${name}暂无商品图`">
      <svg class="size-14 opacity-45" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
        <path d="m8 15 16-8 16 8v20l-16 8-16-8V15Z" /><path d="m8 15 16 8 16-8M24 23v20M16 11l16 8v9" />
      </svg>
      <span class="text-xs tracking-widest">{{ kind || '暂无商品图' }}</span>
    </div>
  </div>
</template>
