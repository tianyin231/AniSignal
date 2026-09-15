<script setup lang="ts">
useSeoMeta({ title: '用户协议 · AniSignal' })

const updated = '2026-09-13'
</script>

<template>
  <div class="space-y-4">
    <BackButton fallback="/legal" label="返回条款索引" />

    <section class="panel p-5 sm:p-6">
      <p class="mono text-xs text-blue-600 dark:text-blue-300">TERMS · 更新于 {{ updated }}</p>
      <h1 class="mt-1 text-lg font-semibold text-slate-900 dark:text-zinc-50">用户协议</h1>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        本协议描述你使用 AniSignal 网站、账号功能、订阅与可选消息通道（含邮件、机器人）时的权利与义务。
        使用本服务即表示你已阅读并同意本协议；若不同意，请停止使用。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">1. 服务是什么</h2>
      <div class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <p>AniSignal 根据管理员配置的采集目标，从已接入的公开来源获取信息，进行筛选、归并与展示，并向已订阅用户提供邮件或机器人等送达方式。</p>
        <p>服务<strong>不是</strong>：全网搜索引擎、实时行情、电商订单查询、个人发货预测，也不构成对游戏公司或商品商家的官方立场。</p>
      </div>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">2. 账号与安全</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />你应使用真实、可接收验证邮件的邮箱注册，并自行保管密码。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />不得共享、出售账号，或利用账号从事违法违规、骚扰、垃圾营销行为。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />发现账号被盗用应尽快改密；改密或重置后系统可能吊销全部会话。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">3. 你可以做什么</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />浏览公开内容、事件、商品进展与来源状态。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />在限额内配置关注词、定制监控与通知偏好。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />在遵守平台规则的前提下，将机器人接入你有权管理的会话。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">4. 禁止行为</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-amber-500" />爬取、镜像或批量导出站内数据用于转售、训练或替代性产品（RSS 仅供个人订阅）。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-amber-500" />干扰服务（刷接口、撞库、恶意注册、滥用定制监控占满配额）。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-amber-500" />利用机器人通道发送垃圾信息、诱导点击、欺诈或违反 QQ/邮件服务商规则的内容。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-amber-500" />将「待核验」商品线索宣传为已证实结论，或用于预测他人订单。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">5. 内容与知识产权</h2>
      <div class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <p>站内展示的标题、摘要、封面等可能来自第三方公开来源，权利归原权利人所有。本站提供检索、归并与外链，便于你回到原文核对。</p>
        <p>你上传或配置的关键词、监控目标等，应保证不侵犯他人合法权益。权利人可按<a href="/copyright" class="link">版权与下架</a>流程投诉。</p>
      </div>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">6. 免责声明</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />服务按「现状」提供，不保证无中断、无错误或覆盖全部平台。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />事件归并与 AI 输出可能不准确；请以原始来源为准。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />邮件、机器人送达依赖第三方通道，不保证对方平台最终展示。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />试运行期间功能、配额与界面可能变更。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">7. 终止与变更</h2>
      <div class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <p>若你违反本协议或法律法规，我们可限制功能、暂停或终止账号，并视情况删除相关配置。</p>
        <p>协议更新后会在本页公示更新日期；重大变更将通过站内公告或邮件提示。继续使用视为接受更新后的条款。</p>
      </div>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">8. 联系</h2>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        协议、账号与投诉相关事宜，请发送邮件至
        <a href="mailto:admin@anisignal.nanamiyako.cn" class="link">admin@anisignal.nanamiyako.cn</a>。
      </p>
      <div class="mt-4 flex flex-wrap gap-4 text-sm">
        <NuxtLink to="/privacy" class="link">隐私政策</NuxtLink>
        <NuxtLink to="/legal" class="link">条款索引</NuxtLink>
        <NuxtLink to="/about" class="link">关于与边界</NuxtLink>
      </div>
    </section>
  </div>
</template>
