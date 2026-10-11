/** 页面可见时轻量刷新；组件卸载后停止，SSR 不启动计时器。 */
export function useVisibleRefresh(refresh: () => Promise<unknown>, enabled: () => boolean = () => true) {
  let timer: ReturnType<typeof setInterval> | undefined
  let refreshing = false
  onMounted(() => {
    timer = setInterval(async () => {
      if (document.visibilityState !== 'visible' || !enabled() || refreshing) return
      refreshing = true
      try { await refresh() }
      catch { /* 请求状态由页面展示，下一轮继续尝试。 */ }
      finally { refreshing = false }
    }, 60_000)
  })
  onBeforeUnmount(() => { if (timer) clearInterval(timer) })
}
