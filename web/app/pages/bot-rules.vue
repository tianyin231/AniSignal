<script setup lang="ts">
useSeoMeta({ title: '机器人守则 · AniSignal' })

const allowed = [
  '在本人管理或获授权的 QQ 群 / 私聊中添加机器人，并配置分区、关键词与静默时段。',
  '使用 /帮助、/早报、/资讯、/商品 等指令查询已授权工具范围内的信息。',
  '按会话需要开启每日早报、合并推送或关键动态提醒；随时可暂停。',
  '向管理员反馈误推、刷屏或错误内容，以便调整采集与投递规则。',
]

const forbidden = [
  '要求机器人向未授权会话、陌生群或大规模群列表进行营销式推送。',
  '利用推送通道发送广告、拉人、返利、赌博、色情或其他违法违规内容。',
  '故意高频触发查询或提醒，干扰服务或其他用户。',
  '将机器人输出宣称为游戏官方结论；待核验商品线索不得当作已到货事实。',
  '绕过频控、合并窗口或平台限制，或协助他人滥用接口。',
]

const limits = [
  { t: '合并窗口', d: '普通动态默认数分钟内合并为一条列表，避免连环刷屏。' },
  { t: '小时上限', d: '每会话每小时推送上限可配；超出后非关键动态顺延。' },
  { t: '静默时段', d: '默认夜间不推普通更新；关键动态是否穿透由会话配置决定。' },
  { t: '添加免费', d: '把机器人加入群聊本身不收费；增值服务以站内说明为准。' },
]
</script>

<template>
  <div class="space-y-4">
    <section class="panel p-5 sm:p-6">
      <p class="mono text-xs text-blue-600 dark:text-blue-300">BOT</p>
      <h1 class="mt-1 text-lg font-semibold text-slate-900 dark:text-zinc-50">机器人使用守则</h1>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        机器人是 AniSignal 的<strong>可选送达通道</strong>，不是独立聊天产品。
        使用 QQ 等通道时，须同时遵守本站条款、平台（如腾讯）规则与群规。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">允许的用法</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li v-for="item in allowed" :key="item" class="flex gap-2">
          <span class="mt-2 size-1 shrink-0 rounded-full bg-emerald-500" />
          {{ item }}
        </li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">禁止的用法</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li v-for="item in forbidden" :key="item" class="flex gap-2">
          <span class="mt-2 size-1 shrink-0 rounded-full bg-rose-500" />
          {{ item }}
        </li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">频控与友好使用</h2>
      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <div v-for="item in limits" :key="item.t" class="rounded-[10px] border border-slate-200/70 p-3 dark:border-zinc-800">
          <p class="text-sm font-medium text-slate-800 dark:text-zinc-200">{{ item.t }}</p>
          <p class="mt-1 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">{{ item.d }}</p>
        </div>
      </div>
      <p class="mt-3 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        群聊场景请优先使用指令或 @ 唤醒；避免要求机器人回应与资讯无关的长篇闲聊，以免占用通道与触发平台风控。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">内容性质</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />推送保留来源与时间；以站内原文与源站为准。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />人格化开场仅为表达风格，不改变资料本身。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />不承诺消息必达；失败可能需在站内投递记录核对。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">违规处理</h2>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        违反本守则或平台规则的会话，可能被暂停推送、移除配置或限制账号功能。
        若你的群被他人误配推送，请联系管理员并提供会话标识以便关闭。
      </p>
      <div class="mt-4 flex flex-wrap gap-4 text-sm">
        <NuxtLink to="/terms" class="link">用户协议</NuxtLink>
        <NuxtLink to="/legal" class="link">条款索引</NuxtLink>
        <NuxtLink to="/about" class="link">关于与边界</NuxtLink>
      </div>
    </section>
  </div>
</template>
