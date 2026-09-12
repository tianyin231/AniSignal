# AniSignal Web

[产品首页](../README.md) · [功能指南](../docs/features.md) · [部署指南](../docs/deployment.md)

基于 Nuxt 4、Tailwind CSS 4 和 TypeScript。前端呈现聚合首页、分区内容、事件证据、商品进展、公开定制监控、账号中心与管理后台；数据来自后端 API，而不是固定写入页面的内容目录。

## 运行与构建

使用 Node.js 22 和 npm，与仓库构建镜像保持一致。从仓库根目录执行：

```bash
cd web
npm ci
npm run dev
```

开发站点为 `http://localhost:3000`，需同时启动后端；见[部署与本地开发](../docs/deployment.md)。生产构建在 `web` 目录执行：

```bash
npm run build
```

`nuxt.config.ts` 中的 `/api/**` 由 Nitro 代理到 `127.0.0.1:8000`，SSR 使用 `apiSsrBase` 直连后端。`/admin/**` 是页面路由，不属于该 API 代理。修改 API 地址时需同时核对浏览器代理与 SSR 配置。

## 页面与结构

| 位置 | 内容 |
| --- | --- |
| `app/pages/index.vue` | 聚合首页与分区视角。 |
| `app/pages/contents/`、`events/`、`products/` | 内容预览、事件证据与商品进展。 |
| `app/pages/custom/`、`account.vue` | 公开独立监控，以及个人关注、监控与通知设置。 |
| `app/pages/official.vue`、`search.vue`、`sources.vue`、`stats.vue` | 官方动态、站内检索、来源健康与统计。 |
| `app/pages/admin/` | 采集目标、内容、事件、商品、分类、用户、站点、AI、机器人与定制监控管理。 |
| `app/layouts/`、`components/` | 页面布局与共享展示组件。 |
| `app/composables/`、`utils/` | API、认证、站点配置、分区状态和数据适配。 |

## 关键约定

- API 调用使用 `usePublicApi()` / `useAdminApi()`；管理接口使用 `X-Admin-Token`。不要将管理凭据写入页面文案、示例链接或公开资源。
- 站点文案由 `useSiteConfig()` 读取。当前采集主题不等于固定产品范围；新增分区要核对注册、导航、过滤条件与查询缓存，不能只替换标签。
- 认证页与账号中心使用独立布局。管理端异步操作应提供忙碌状态与 toast，不使用 `alert()`。
- 保持现有明暗主题、玻璃面板与论坛蓝主色约定，不引入紫色系；时间格式沿用项目公共格式化工具。
- 涉及模板和交互的修改需进行浏览器检查；`npm run build` 成功不能代替页面、响应式布局与实际数据状态验证。

更多维护背景见[项目参考](../.agents/skills/anisignal/SKILL.md)。功能范围与当前状态以代码及[功能指南](../docs/features.md)为准，不沿用历史页面数量或固定内容主题。
