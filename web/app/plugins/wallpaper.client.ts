// 客户端启动时恢复用户上次选择的壁纸偏好（只注册一次，避免多组件重复触发）
export default defineNuxtPlugin(() => {
  const { restore } = useWallpaper()
  restore()
})
