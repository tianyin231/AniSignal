<script setup lang="ts">
/**
 * 统一侧栏横向分布条卡片：复用于开源「语言分布」与主题流「热门话题」。
 */
export interface BarDistributionItem {
  name: string
  count: number
  color?: string
}

const props = withDefaults(defineProps<{
  title: string
  items: BarDistributionItem[]
  active?: string
  limit?: number
}>(), {
  active: '',
  limit: 8,
})

const emit = defineEmits<{
  select: [name: string]
  reset: []
}>()

const shown = computed(() => props.items.slice(0, props.limit))
const maxCount = computed(() => Math.max(1, ...shown.value.map(i => i.count)))
</script>

<template>
  <section v-if="shown.length" class="panel p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="inline-flex items-center gap-1.5 text-sm font-bold text-ink">
        <slot name="icon" />
        {{ title }}
      </h2>
      <button
        v-if="active"
        type="button"
        class="text-[11px] text-brand-ink hover:underline"
        @click="emit('reset')"
      >
        重置
      </button>
    </div>
    <ul class="mt-3 space-y-2.5">
      <li v-for="item in shown" :key="item.name">
        <button type="button" class="group w-full text-left" @click="emit('select', item.name)">
          <div class="flex items-center justify-between gap-2 text-xs">
            <span
              class="inline-flex min-w-0 items-center gap-1.5 font-medium"
              :class="active === item.name ? 'text-brand-ink' : 'text-ink-2 group-hover:text-ink'"
            >
              <span
                v-if="item.color"
                class="size-2 shrink-0 rounded-full"
                :style="{ backgroundColor: item.color }"
                aria-hidden="true"
              />
              <span class="truncate">{{ item.name }}</span>
            </span>
            <span class="num shrink-0 tabular-nums text-muted">{{ item.count }}</span>
          </div>
          <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-chip">
            <div
              class="h-full rounded-full transition-all"
              :class="!item.color ? (active === item.name ? 'bg-brand' : 'bg-accent/70') : ''"
              :style="{
                width: `${Math.max(10, Math.round((item.count / maxCount) * 100))}%`,
                ...(item.color ? { backgroundColor: item.color } : {}),
              }"
            />
          </div>
        </button>
      </li>
    </ul>
  </section>
</template>
