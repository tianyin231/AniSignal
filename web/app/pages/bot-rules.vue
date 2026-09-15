<script setup lang="ts">
useSeoMeta({
  title: '机器人守则 · AniSignal',
  description: 'AniSignal 爬虫与消息机器人守则（robots.txt 风格）',
})

const crawlerRules = `# AniSignal robots.txt
# https://anisignal.nanamiyako.cn/robots.txt

User-agent: *
Allow: /
Allow: /intro
Allow: /about
Allow: /legal
Allow: /terms
Allow: /privacy
Allow: /copyright
Allow: /bot-rules
Allow: /official
Allow: /contents
Allow: /events
Allow: /products
Allow: /sources
Allow: /stats
Allow: /search
Allow: /custom
Allow: /rss.xml

Disallow: /admin
Disallow: /account
Disallow: /api/
Disallow: /login
Disallow: /register
Disallow: /forgot

Crawl-delay: 10

Sitemap: https://anisignal.nanamiyako.cn/rss.xml`

const messageBotRules = `# ── 消息机器人（QQ / 邮件等通道）──
# 本段不是 HTTP robots.txt 语法，供人阅读；不供爬虫解析。

# User-agent: AniSignal-Bot
# 身份: AniSignal 试运行站资讯通道，非腾讯/QQ 官方

Allow:
  /帮助
  /早报
  /资讯 <关键词>
  /商品 <关键词>
  /情报会话
  /情报订阅
  /我的提醒
  会话内已授权的查询工具

Disallow:
  向未授权会话营销式推送
  广告 / 拉人 / 返利 / 违法违规内容
  高频刷接口、绕过频控与静默
  将待核验线索宣称为已证实事实

# 频控（会话可配，默认建议）
Merge-Window: 5m
Hourly-Limit: 6
Quiet-Hours: 23-8
Max-Images: 3

# 进群
Add-To-Group: free
# 添加本身不收费；增值服务以站内说明为准

# 守则全文
Policy: https://anisignal.nanamiyako.cn/bot-rules
Terms:  https://anisignal.nanamiyako.cn/terms`

const lines = computed(() => [
  ...crawlerRules.split('\n'),
  '',
  ...messageBotRules.split('\n'),
])
</script>

<template>
  <div class="space-y-4">
    <BackButton fallback="/legal" label="返回条款索引" />

    <section class="panel p-5 sm:p-6">
      <p class="mono text-xs text-blue-600 dark:text-blue-300">ROBOTS</p>
      <h1 class="mt-1 text-lg font-semibold text-slate-900 dark:text-zinc-50">机器人守则</h1>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        与
        <a href="/robots.txt" class="link mono" target="_blank" rel="noopener">/robots.txt</a>
        同源的规则说明：上半段给网络爬虫，下半段给 QQ 等消息通道。
        原始文件可被程序抓取；本页便于阅读。
      </p>
    </section>

    <section class="panel overflow-hidden p-0">
      <div
        class="flex items-center justify-between border-b border-slate-200/80 px-4 py-2.5 dark:border-zinc-800"
      >
        <span class="mono text-xs text-slate-500 dark:text-zinc-400">robots.txt</span>
        <a
          href="/robots.txt"
          class="mono text-xs text-blue-600 hover:underline dark:text-blue-300"
          target="_blank"
          rel="noopener"
        >
          下载原始文件
        </a>
      </div>
      <pre
        class="overflow-x-auto bg-slate-50 px-4 py-4 text-[12px] leading-relaxed text-slate-700 dark:bg-zinc-950 dark:text-zinc-300"
      ><code><span
          v-for="(line, i) in lines"
          :key="i"
          class="block min-h-[1.25em]"
          :class="{
            'text-slate-400 dark:text-zinc-600': line.startsWith('#'),
            'font-medium text-blue-700 dark:text-blue-300': /^(User-agent|Allow|Disallow|Crawl-delay|Sitemap|Merge-Window|Hourly-Limit|Quiet-Hours|Max-Images|Add-To-Group|Policy|Terms):/.test(line),
          }"
        >{{ line || ' ' }}</span></code></pre>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">说明</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2">
          <span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />
          公开内容页可被索引；管理、账号与 <span class="mono text-xs">/api/</span> 禁止抓取。
        </li>
        <li class="flex gap-2">
          <span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />
          消息机器人只在已授权会话工作；添加进群不收费，禁止营销滥用。
        </li>
        <li class="flex gap-2">
          <span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />
          完整法律文本见
          <NuxtLink to="/terms" class="link">用户协议</NuxtLink>
          与
          <NuxtLink to="/privacy" class="link">隐私政策</NuxtLink>。
        </li>
      </ul>
      <div class="mt-4 flex flex-wrap gap-4 text-sm">
        <NuxtLink to="/legal" class="link">条款索引</NuxtLink>
        <NuxtLink to="/terms" class="link">用户协议</NuxtLink>
        <a href="mailto:admin@anisignal.nanamiyako.cn" class="link">联系管理员</a>
      </div>
    </section>
  </div>
</template>
