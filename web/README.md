# AniSignal Web（Nuxt 4 + Tailwind 4）

全站真实数据驱动：首页（要闻/官方/来源动态三 tab）、内容库 `/contents`、内容详情 `/contents/[id]`（站内解析预览）、事件库、商品履约、来源健康、搜索、账号中心（登录后）与管理看板 `/admin`。

## 命令

```bash
npm install
npm run dev     # 开发服务器（API 默认走同源代理到 127.0.0.1:8000，配合 server 的 app.local）
npm run build   # 生产构建
```

## 结构

```text
app/
├── layouts/       # default（三栏）admin auth（认证）account（账号中心）
├── pages/         # 首页/official/sources/events/products/contents/search/watch/login/register/forgot/account + admin/*（7 页）
├── components/    # EventRow RawFeedRow FulfillmentStepper SourceTable AnnouncementBanner AdminToasts TodayBriefCard …
├── composables/   # usePublicApi useAdminApi useAuth useSiteConfig useAdminToast
└── utils/         # data.ts（类型+平台/身份元数据）public-utils.ts（适配器）avatars.ts admin-types.ts
```

## 关键约定

- **API 调用**：`usePublicApi()`（公开）/ `useAdminApi()`（管理，X-Admin-Token）；浏览器走同源（Nitro routeRules 代理 `/api/**` → 127.0.0.1:8000），SSR 直连 `apiSsrBase`。
- 页面文案走站点配置：`useSiteConfig().t('key')`（默认值在 useSiteConfig.ts，与后端 DEFAULT_TEXTS 对应）。
- 认证三页/账号中心用独立 layout；登录注册后跳 `/account`。
- 管理端所有异步操作必须有 busy 文案 + toast（useAdminToast），禁止 alert()。
- 材质参考 macOS：玻璃 panel/aurora 背景，主色论坛蓝，**全站无紫色系**；身份标签色系见 data.ts identityMeta。
- 时间戳统一 `utils/admin-types.ts fmtTime`（YYYY/MM/DD HH:mm，北京时间）。
- **改模板后必须浏览器实测**（DOM snapshot/截图），构建通过不代表结构正确。
