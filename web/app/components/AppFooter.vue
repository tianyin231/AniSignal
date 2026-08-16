<template>
  <footer class="mt-6 border-t border-slate-200/80 dark:border-zinc-800">
    <div
      class="mx-auto flex max-w-[1280px] flex-col gap-2 px-4 py-6 text-xs text-slate-400 dark:text-zinc-500 sm:flex-row sm:items-center sm:justify-between lg:px-6"
    >
      <p>AniSignal 前端原型 · 演示数据，非实时 · 不承诺全网覆盖</p>
      <nav class="flex gap-4">
        <NuxtLink to="/about" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300">关于</NuxtLink>
        <a href="#" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300" @click.prevent>隐私与个人信息</a>
        <a href="#" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300" @click.prevent>权利人投诉</a>
        <a href="#" class="transition-colors hover:text-slate-600 dark:hover:text-zinc-300" @click.prevent>退订</a>
      </nav>
    </div>
  </footer>
</template>
