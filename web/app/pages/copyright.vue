<script setup lang="ts">
useSeoMeta({ title: '版权与下架 · AniSignal' })

const steps = [
  {
    n: '01',
    t: '准备材料',
    d: '说明你是权利人或授权代理人；提供涉嫌侵权内容在本站的链接（/contents/… 或事件页）、原始出处链接，以及主张权利的作品说明。',
  },
  {
    n: '02',
    t: '发送邮件',
    d: '发至 admin@anisignal.nanamiyako.cn，主题含「版权投诉」。请附联系方式，便于我们回执与核对。',
  },
  {
    n: '03',
    t: '受理与核查',
    d: '我们会在合理期限内核对来源与主张；必要时要求补充权属证明。明显无依据或恶意投诉可能不予处理。',
  },
  {
    n: '04',
    t: '处理结果',
    d: '核实后将下架、限制展示或修正错误归并，并在站内记录处理状态。已发出的邮件/机器人消息无法撤回，但站内详情会更新。',
  },
]
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-5 sm:p-6">
      <p class="mono text-xs text-blue-600 dark:text-blue-300">COPYRIGHT</p>
      <h1 class="mt-1 text-lg font-semibold text-slate-900 dark:text-zinc-50">版权与删除 / 下架流程</h1>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        AniSignal 优先聚合<strong>公开可访问</strong>的官方公告与社区线索，展示标题、摘要与外链，便于用户回到原文。
        若你认为站内内容侵犯了你的合法权益，可通过下列流程要求处理。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">我们展示什么</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />通常包括：标题、时间、来源标识、摘要片段、封面预览、原始链接。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />事件页会归并多条相关线索并标注证据；不替代原文阅读。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />源站删除或修改后，站内可能仍保留历史解析版本，但会尽量同步可见性。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">权利人投诉步骤</h2>
      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <div v-for="s in steps" :key="s.n" class="rounded-[10px] border border-slate-200/70 p-3 dark:border-zinc-800">
          <p class="mono text-xs text-blue-600 dark:text-blue-300">{{ s.n }}</p>
          <p class="mt-0.5 text-sm font-medium text-slate-800 dark:text-zinc-200">{{ s.t }}</p>
          <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ s.d }}</p>
        </div>
      </div>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">投诉邮件建议包含</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />你的姓名 / 机构与联系方式。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />权属说明（著作权、商标、肖像或其他合法权益）。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />涉嫌侵权内容的<strong>本站 URL</strong> 与对应的原始出处。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />请求：删除预览 / 下架条目 / 修正错误归并 / 其他。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />声明投诉出于善意且信息真实（可参照通行的声明格式）。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">用户侧更正与反馈</h2>
      <div class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <p>若发现事件误归并、商品「待核验」被误读、来源标错等，也可来信说明，附事件/内容链接与证据。我们将人工复核，必要时锁定事件并写修订记录。</p>
        <p>站内隐藏的内容不会出现在公开流、精选与证据列表；数据库中的原始记录可能为审计与版本追溯保留。</p>
      </div>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">联系方式</h2>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        版权与下架：
        <a href="mailto:admin@anisignal.nanamiyako.cn" class="link">admin@anisignal.nanamiyako.cn</a>
      </p>
      <div class="mt-4 flex flex-wrap gap-4 text-sm">
        <NuxtLink to="/terms" class="link">用户协议</NuxtLink>
        <NuxtLink to="/about" class="link">关于与边界</NuxtLink>
        <NuxtLink to="/legal" class="link">条款索引</NuxtLink>
      </div>
    </section>
  </div>
</template>
