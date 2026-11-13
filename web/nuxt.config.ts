import tailwindcss from '@tailwindcss/vite'

// 默认连本机 8000；容器化拆分部署在运行时通过 NUXT_API_SSR_BASE（如 http://api:8000）覆盖，无需重新构建镜像。
const apiProxy = process.env.NUXT_API_SSR_BASE || process.env.ANISIGNAL_API_PROXY || 'http://127.0.0.1:8000'

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: false },
  // 私有项目不需要遥测；同时避免首跑交互提示阻塞终端
  telemetry: false,
  css: ['~/assets/css/main.css'],
  nitro: { compressPublicAssets: true },
  runtimeConfig: {
    // SSR 与 Nitro API 反代运行时直连 FastAPI（由 NUXT_API_SSR_BASE 注入）
    apiSsrBase: apiProxy,
    public: {
      // 浏览器端走同源（默认空串），由 Nitro 运行时中间件代理到 FastAPI
      apiBase: '',
      // 规范地址和分享卡片用的公开域名，可用 NUXT_PUBLIC_SITE_URL 覆盖；留空自动取当前请求源
      siteUrl: '',
    },
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
