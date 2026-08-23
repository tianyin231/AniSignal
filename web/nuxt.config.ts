import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: false },
  // 私有项目不需要遥测；同时避免首跑交互提示阻塞终端
  telemetry: false,
  css: ['~/assets/css/main.css'],
  runtimeConfig: {
    public: {
      // 后端 FastAPI 地址；生产用 NUXT_PUBLIC_API_BASE 覆盖
      apiBase: 'http://localhost:8000',
    },
  },
  vite: {
    plugins: [tailwindcss()],
  },
  app: {
    head: {
      htmlAttrs: { lang: 'zh-CN' },
      title: 'AniSignal · 鸣潮事件雷达',
      meta: [
        { name: 'description', content: '《鸣潮》跨平台事件聚合与商品履约追踪（原型演示数据）' },
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
