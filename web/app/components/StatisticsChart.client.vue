<script setup lang="ts">
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { init, use, type EChartsCoreOption, type EChartsType } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

use([
  BarChart,
  LineChart,
  PieChart,
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])

const props = defineProps<{
  option: EChartsCoreOption
  ariaLabel: string
  height?: string
}>()

const root = ref<HTMLElement | null>(null)
const theme = useState<'light' | 'dark'>('theme', () => 'light')
let chart: EChartsType | undefined
let resizeObserver: ResizeObserver | undefined

function createChart() {
  if (!root.value) return
  chart?.dispose()
  chart = init(root.value, theme.value === 'dark' ? 'dark' : undefined, { renderer: 'canvas' })
  chart.setOption({ backgroundColor: 'transparent', ...props.option })
}

watch(
  () => props.option,
  option => chart?.setOption({ backgroundColor: 'transparent', ...option }, true),
  { deep: true },
)
watch(theme, () => nextTick(createChart))

onMounted(() => {
  createChart()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  if (root.value) resizeObserver.observe(root.value)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div
    ref="root"
    class="w-full"
    :style="{ height: height || '18rem' }"
    role="img"
    :aria-label="ariaLabel"
  />
</template>
