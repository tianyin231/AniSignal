<script setup lang="ts">
useSeoMeta({ title: '隐私政策 · AniSignal' })

const updated = '2026-09-13'
</script>

<template>
  <div class="space-y-4">
    <BackButton fallback="/legal" label="返回条款索引" />

    <section class="panel p-5 sm:p-6">
      <p class="mono text-xs text-blue-600 dark:text-blue-300">PRIVACY · 更新于 {{ updated }}</p>
      <h1 class="mt-1 text-2xl font-bold text-ink">隐私政策</h1>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        本政策说明 AniSignal 在提供采集展示、账号订阅与消息送达时，如何收集、使用与保护个人信息。
        我们尽量少收集，并只为提供服务而处理数据。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-base font-bold text-ink">1. 我们收集什么</h2>
      <div class="mt-3 overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead>
            <tr class="border-b border-slate-200 text-xs text-slate-500 dark:border-zinc-800 dark:text-zinc-400">
              <th class="py-2 pr-4 font-medium">类别</th>
              <th class="py-2 pr-4 font-medium">示例</th>
              <th class="py-2 font-medium">用途</th>
            </tr>
          </thead>
          <tbody class="align-top text-slate-600 dark:text-zinc-300">
            <tr class="border-b border-slate-100 dark:border-zinc-900">
              <td class="py-2.5 pr-4">账号信息</td>
              <td class="py-2.5 pr-4">邮箱、昵称、头像选择、密码哈希</td>
              <td class="py-2.5">登录、验证、找回密码、展示</td>
            </tr>
            <tr class="border-b border-slate-100 dark:border-zinc-900">
              <td class="py-2.5 pr-4">订阅配置</td>
              <td class="py-2.5 pr-4">关注词、定制监控、通知开关、邮件设置</td>
              <td class="py-2.5">生成关注流与投递</td>
            </tr>
            <tr class="border-b border-slate-100 dark:border-zinc-900">
              <td class="py-2.5 pr-4">投递记录</td>
              <td class="py-2.5 pr-4">邮件/机器人发送状态、时间、类型</td>
              <td class="py-2.5">审计、重试、排障</td>
            </tr>
            <tr class="border-b border-slate-100 dark:border-zinc-900">
              <td class="py-2.5 pr-4">会话标识</td>
              <td class="py-2.5 pr-4">机器人 UMO、群/私聊标识、管理授权名单</td>
              <td class="py-2.5">将推送到正确会话</td>
            </tr>
            <tr>
              <td class="py-2.5 pr-4">技术日志</td>
              <td class="py-2.5 pr-4">访问时间、错误、限流计数（尽量不长期存原始 IP）</td>
              <td class="py-2.5">安全与稳定性</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="mt-3 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
        站内「内容」多为公开来源的标题/摘要/链接，不是你的私人聊天记录。你配置的关键词可能含游戏名或商品名，一般不视为敏感个人信息；请勿填入身份证号、银行卡等无关数据。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-base font-bold text-ink">2. 我们如何使用</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />验证身份、维护会话安全（登录、改密吊销会话）。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />按你的关注词与监控配置筛选站内内容，生成关注流。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />向你开启的邮箱或机器人会话发送早报、提醒与动态。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />统计服务用量（如 AI 调用次数），用于运维与限流。</li>
      </ul>
      <p class="mt-3 text-sm leading-relaxed text-slate-500 dark:text-zinc-400">
        我们<strong>不会</strong>出售你的个人信息，也不会将你的邮箱与订阅内容用于与本服务无关的广告投放。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-base font-bold text-ink">3. 第三方与外发</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" /><strong>邮件</strong>：通过你部署或本站配置的 SMTP 发送；服务商可见收件地址与邮件正文。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" /><strong>机器人</strong>：消息经 AstrBot 与所选平台（如 QQ）投递；平台侧可见会话内消息。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" /><strong>AI 模型</strong>：若启用摘要/翻译/筛选，相关内容会发送给配置的模型接口；请避免在关注词中写入隐私。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" /><strong>公开来源</strong>：浏览时你可能跳转到 B 站、官网等，其隐私政策由对方制定。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-base font-bold text-ink">4. 保存与安全</h2>
      <div class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <p>密码使用不可逆哈希存储；会话令牌为随机值。传输层建议在正式部署启用 HTTPS。</p>
        <p>投递与审计记录会保留一段时间以便排障；账号注销或删除请求后，我们将删除或匿名化可识别个人的数据（法律要求保留的除外）。</p>
        <p>任何系统都无法保证绝对安全；若发生可能影响你权益的数据事件，我们将按法律要求告知。</p>
      </div>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-base font-bold text-ink">5. 你的权利</h2>
      <ul class="mt-2 space-y-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />在账号中心查看、修改关注词、定制监控与通知设置。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />关闭邮件或机器人推送；从会话中移除机器人。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />申请导出或删除账号相关数据（邮件联系我们）。</li>
        <li class="flex gap-2"><span class="mt-2 size-1 shrink-0 rounded-full bg-blue-500" />对公开内容提出更正或下架（见版权流程）。</li>
      </ul>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-base font-bold text-ink">6. Cookie 与本地存储</h2>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        登录使用 httpOnly Cookie 保存会话；主题、壁纸等偏好可能存在浏览器 localStorage。清除 Cookie 会导致退出登录。
      </p>
    </section>

    <section class="panel p-5 sm:p-6">
      <h2 class="text-base font-bold text-ink">7. 联系我们</h2>
      <p class="mt-2 text-sm leading-relaxed text-slate-600 dark:text-zinc-300">
        隐私相关请求请邮件：
        <a href="mailto:admin@anisignal.nanamiyako.cn" class="link">admin@anisignal.nanamiyako.cn</a>
        （主题可写「隐私请求」）。
      </p>
      <div class="mt-4 flex flex-wrap gap-4 text-sm">
        <NuxtLink to="/terms" class="link">用户协议</NuxtLink>
        <NuxtLink to="/legal" class="link">条款索引</NuxtLink>
      </div>
    </section>
  </div>
</template>
