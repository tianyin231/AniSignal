<script setup lang="ts">
import { Moon, Sun } from 'lucide-vue-next'

const theme = useState<'light' | 'dark'>('theme', () => 'light')

onMounted(() => {
  theme.value = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
})

function toggle() {
  const next = theme.value === 'dark' ? 'light' : 'dark'
  theme.value = next
  document.documentElement.classList.toggle('dark', next === 'dark')
  document.documentElement.dataset.theme = next
  try {
    localStorage.setItem('anisignal-theme', next)
  } catch {}
}
</script>

<template>
  <button
    type="button"
    :aria-label="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
    class="grid size-10 place-items-center rounded-[10px] text-muted transition-colors hover:bg-chip hover:text-ink"
    @click="toggle"
  >
    <Sun v-if="theme === 'dark'" :size="18" />
    <Moon v-else :size="18" />
  </button>
</template>
