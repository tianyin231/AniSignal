// 客户端启动时恢复用户上次选择的壁纸偏好（只注册一次，避免多组件重复触发）
export default defineNuxtPlugin(() => {
  const { restore } = useWallpaper()
  // 等待异步页面完成水合，再恢复背景，避免按钮与服务端节点不一致。
  onNuxtReady(restore)
})
