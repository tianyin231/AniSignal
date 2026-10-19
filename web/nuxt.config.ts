import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: false },
  // 私有项目不需要遥测；同时避免首跑交互提示阻塞终端
  telemetry: false,
  css: ['~/assets/css/main.css'],
  nitro: { compressPublicAssets: true },
  runtimeConfig: {
    // SSR 服务端直连 FastAPI（app 容器内同网络命名空间）
    apiSsrBase: 'http://127.0.0.1:8000',
    public: {
      // 浏览器端走同源（默认空串），由 Nitro 代理到 FastAPI，不再暴露 API 端口
      apiBase: '',
    },
  },
  routeRules: {
    // 后端所有 HTTP API 都在 /api/** 下（含 /api/admin 管理接口）；
    // /admin/** 是 Nuxt 页面路由，不代理
    '/api/**': { proxy: 'http://127.0.0.1:8000/api/**' },
    // RSS 订阅源：后端生成 XML，这里映射到站点根路径
    '/rss.xml': { proxy: 'http://127.0.0.1:8000/api/rss.xml' },
  },
  vite: {
    plugins: [tailwindcss()],
  },
  app: {
    head: {
      htmlAttrs: { lang: 'zh-CN' },
      title: 'AniSignal · 让重要的变化，找到你',
      link: [
        { rel: 'icon', type: 'image/png', href: '/favicon.png' },
        { rel: 'apple-touch-icon', href: '/apple-touch-icon.png' },
      ],
      meta: [
        { name: 'description', content: '自托管信号台。公开更新汇成可追溯的事件，并按你的关注送达。' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
      ],
      script: [
        {
          // 首帧前恢复主题，避免暗色用户闪白（无依赖的手工实现）
          innerHTML:
            "(function(){try{var t=localStorage.getItem('anisignal-theme');if(t!=='dark'&&t!=='light'){t=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.classList.toggle('dark',t==='dark');document.documentElement.dataset.theme=t}catch(e){}})()",
        },
      ],
    },
  },
})
