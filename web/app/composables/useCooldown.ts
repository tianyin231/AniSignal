/** 发送验证码的冷却倒计时（后端同一邮箱 1 分钟内只发一次）。 */
export function useCooldown(seconds = 60) {
  const remaining = ref(0)
  let timer: ReturnType<typeof setInterval> | undefined

  function stop() {
    if (timer) clearInterval(timer)
    timer = undefined
  }

  function start(duration = seconds) {
    stop()
    remaining.value = duration
    timer = setInterval(() => {
      remaining.value -= 1
      if (remaining.value <= 0) stop()
    }, 1000)
  }

  onBeforeUnmount(stop)
  return { remaining, start }
}
