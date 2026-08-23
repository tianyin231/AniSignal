<script setup lang="ts">
// 每日早报尚未上线：诚实占位，不用演示数据冒充
</script>

<template>
  <section class="panel-glass p-4">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-900 dark:text-zinc-100">每日早报</h2>
      <span class="chip bg-slate-500/10 text-slate-500 dark:text-zinc-400">规划中</span>
    </div>
    <p class="mt-2 text-xs leading-relaxed text-slate-500 dark:text-zinc-400">
      每天 08:00（北京时间）汇总前一日官方动态与商品履约变化，重要状态变化不等待早报即时发送。
      邮件通知与订阅功能上线前，可随时通过本站免费查看全部信息。
    </p>
    <NuxtLink to="/watch" class="link mt-2 inline-block text-xs">了解订阅规划 →</NuxtLink>
  </section>
</template>
