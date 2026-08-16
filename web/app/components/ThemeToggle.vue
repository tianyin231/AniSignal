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
    class="grid size-8 place-items-center rounded-md text-slate-500 transition-colors hover:bg-slate-200/70 hover:text-slate-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100"
    @click="toggle"
  >
    <Sun v-if="theme === 'dark'" :size="16" />
    <Moon v-else :size="16" />
  </button>
</template>
