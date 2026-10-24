<script setup lang="ts">
const props = defineProps<{ images: string[]; name: string; kind?: string | null; sourceUrl?: string | null }>()
const selected = ref(0)
const dialog = ref<HTMLDialogElement | null>(null)
watch(() => props.images, () => { selected.value = 0 })
const current = computed(() => props.images[selected.value])
</script>

<template>
  <div class="min-w-0 space-y-3">
    <button v-if="current" type="button" class="block w-full cursor-zoom-in rounded-xl focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand" :aria-label="`放大${name}商品图`" @click="dialog?.showModal()">
      <ProductArtwork :src="current" :name="name" :kind="kind" priority class="aspect-[4/5] rounded-xl" />
    </button>
    <ProductArtwork v-else :name="name" :kind="kind" class="aspect-[4/5] rounded-xl" />
    <div v-if="images.length > 1" class="flex gap-2 overflow-x-auto pb-1" aria-label="商品图片">
      <button v-for="(src, index) in images" :key="src" type="button" class="size-14 shrink-0 cursor-pointer rounded-lg border-2 p-0.5 focus-visible:outline-2 focus-visible:outline-brand" :class="selected === index ? 'border-accent' : 'border-transparent hover:border-line'" :aria-label="`查看第 ${index + 1} 张商品图`" :aria-pressed="selected === index" @click="selected = index">
        <ProductArtwork :src="src" :name="name" thumbnail class="size-full rounded" />
      </button>
    </div>
    <a v-if="sourceUrl && current" :href="sourceUrl" target="_blank" rel="noopener noreferrer" class="link inline-flex min-h-11 items-center text-xs">官方图片出处 ↗</a>
    <dialog ref="dialog" class="m-auto max-h-[95dvh] max-w-[95vw] overflow-visible rounded-xl border-0 bg-transparent p-0 backdrop:bg-black/85" :aria-label="`${name}商品图预览`" @click="event => { if (event.target === event.currentTarget) dialog?.close() }">
      <div class="relative">
        <img v-if="current" :src="current" :alt="`${name}官方商品图`" referrerpolicy="no-referrer" class="max-h-[85dvh] max-w-[92vw] rounded-lg object-contain">
        <button type="button" class="absolute right-2 top-2 min-h-11 min-w-11 cursor-pointer rounded-full bg-black/70 px-3 text-sm text-white hover:bg-black focus-visible:outline-2 focus-visible:outline-white" aria-label="关闭商品图预览" @click="dialog?.close()">关闭</button>
      </div>
    </dialog>
  </div>
</template>
