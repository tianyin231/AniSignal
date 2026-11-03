<script setup lang="ts">
import { ArrowUpRight, ChevronDown, GitBranch, GitCommitHorizontal, GitFork, MessageSquare, Star, Tag, Users } from 'lucide-vue-next'
import { fmtTime } from '~/utils/admin-types'
import { PROJECT_GITHUB_URL, PROJECT_REPOSITORY, type ProjectOverview } from '~/utils/project'

useSeoMeta({ title: '开源与更新 · AniSignal', description: '查看 AniSignal 的 GitHub 项目、贡献者与最近更新。' })

const { api } = usePublicApi()
const { data, status, error } = await useLazyAsyncData('project-github',
  (_app, { signal }) => api<ProjectOverview>('/api/project/github', { signal }),
)
const repository = computed(() => data.value?.repository)
const commits = computed(() => data.value?.commits ?? [])
const contributors = computed(() => data.value?.contributors ?? [])
const repoUrl = computed(() => repository.value?.url ?? PROJECT_GITHUB_URL)
const commitsUrl = computed(() => `${repoUrl.value}/commits/${encodeURIComponent(repository.value?.default_branch ?? 'main')}`)
const count = (value?: number | null) => value == null ? '—' : value.toLocaleString('zh-CN')
const failedAvatars = ref<string[]>([])
const avatarFailed = (url: string) => { if (!failedAvatars.value.includes(url)) failedAvatars.value.push(url) }
</script>

<template>
  <div class="space-y-6">
    <header class="panel overflow-hidden">
      <div class="flex flex-wrap items-start gap-5 p-5 sm:p-7">
        <span class="grid size-14 shrink-0 place-items-center rounded-2xl border border-line bg-canvas text-ink" aria-hidden="true"><GitHubMark :size="31" /></span>
        <div class="min-w-0 flex-1">
          <p class="mono break-all text-xs text-muted">{{ repository?.full_name ?? PROJECT_REPOSITORY }}</p>
          <h1 class="mt-1 text-2xl font-bold tracking-tight text-ink sm:text-3xl">开源与更新</h1>
          <p class="mt-2 max-w-2xl text-sm leading-relaxed text-muted">{{ repository?.description || '项目代码、最近更新与参与贡献的伙伴。' }}</p>
        </div>
        <a :href="repoUrl" target="_blank" rel="noopener noreferrer" class="btn-primary inline-flex min-h-11 w-full items-center justify-center gap-2 px-4 text-sm sm:w-auto">
          查看仓库 <ArrowUpRight :size="16" aria-hidden="true" />
        </a>
      </div>
      <dl class="grid grid-cols-3 border-t border-line-soft bg-canvas/40">
        <div class="px-5 py-4 sm:px-7">
          <dt class="flex items-center gap-1.5 text-xs text-muted"><Star :size="14" aria-hidden="true" /> Star</dt>
          <dd class="num mt-1 text-2xl font-bold text-ink">{{ count(repository?.stars) }}</dd>
        </div>
        <div class="border-x border-line-soft px-5 py-4 sm:px-7">
          <dt class="flex items-center gap-1.5 text-xs text-muted"><GitFork :size="14" aria-hidden="true" /> Fork</dt>
          <dd class="num mt-1 text-2xl font-bold text-ink">{{ count(repository?.forks) }}</dd>
        </div>
        <div class="px-5 py-4 sm:px-7">
          <dt class="flex items-center gap-1.5 text-xs text-muted"><Users :size="14" aria-hidden="true" /> 贡献者</dt>
          <dd class="num mt-1 text-2xl font-bold text-ink">{{ data?.fetched_at || contributors.length ? `${contributors.length}${data?.contributors_has_more ? '+' : ''}` : '—' }}</dd>
        </div>
      </dl>
    </header>

    <p v-if="error || data?.stale" role="status" class="rounded-xl border border-warn/25 bg-warn/5 px-4 py-3 text-sm text-ink-2">
      GitHub 的部分数据暂时无法同步<span v-if="data?.fetched_at">，正在显示上次读取的记录</span>。也可以<a :href="repoUrl" target="_blank" rel="noopener noreferrer" class="link">直接访问仓库</a>。
    </p>

    <div class="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_310px]">
      <section class="panel min-w-0 overflow-hidden" aria-labelledby="updates-title" :aria-busy="status === 'pending'">
        <div class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-4 sm:px-6">
          <h2 id="updates-title" class="flex items-center gap-2 text-base font-bold text-ink"><GitCommitHorizontal :size="18" class="text-brand" aria-hidden="true" /> 最近更新</h2>
          <span v-if="repository?.default_branch" class="mono ml-auto inline-flex items-center gap-1.5 rounded-full bg-chip px-2.5 py-1 text-[11px] text-ink-2"><GitBranch :size="12" aria-hidden="true" />{{ repository.default_branch }}</span>
        </div>

        <div v-if="status === 'pending' && !data" role="status" class="space-y-5 px-5 py-7 sm:px-6">
          <span class="sr-only">正在加载 GitHub 更新记录…</span>
          <div v-for="i in 4" :key="i" class="space-y-2.5 motion-safe:animate-pulse" aria-hidden="true"><div class="h-4 w-3/4 rounded bg-chip" /><div class="h-3 w-1/2 rounded bg-chip" /></div>
        </div>
        <ol v-else-if="commits.length" class="divide-y divide-line-soft">
          <li v-for="(commit, index) in commits" :key="commit.sha" class="relative flex gap-3 px-5 py-5 sm:gap-4 sm:px-6">
            <span class="relative mt-0.5 grid size-6 shrink-0 place-items-center" aria-hidden="true">
              <span v-if="index !== commits.length - 1" class="absolute left-1/2 top-7 h-[calc(100%+3rem)] w-px bg-line-soft" />
              <span class="relative grid size-6 place-items-center rounded-full" :class="index === 0 ? 'bg-brand-soft text-brand-ink' : 'bg-chip text-muted'"><GitCommitHorizontal :size="14" /></span>
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-start gap-x-2 gap-y-1">
                <a :href="commit.url" target="_blank" rel="noopener noreferrer" class="min-w-0 break-words text-[15px] font-semibold leading-relaxed text-ink transition-colors hover:text-brand-ink">{{ commit.title }}</a>
                <span v-if="index === 0" class="mt-0.5 shrink-0 rounded bg-brand-soft px-1.5 py-0.5 text-[10px] font-semibold text-brand-ink">最新</span>
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                <a :href="commit.url" target="_blank" rel="noopener noreferrer" class="mono inline-flex min-h-6 items-center rounded bg-chip px-1.5 text-[11px] text-ink-2 transition-colors hover:text-brand-ink" :aria-label="`查看提交 ${commit.sha.slice(0, 7)}`">{{ commit.sha.slice(0, 7) }}</a>
                <component :is="commit.author.url ? 'a' : 'span'" :href="commit.author.url || undefined" :target="commit.author.url ? '_blank' : undefined" :rel="commit.author.url ? 'noopener noreferrer' : undefined" class="inline-flex items-center gap-1.5" :class="{ 'hover:text-ink': commit.author.url }">
                  <img v-if="commit.author.avatar_url && !failedAvatars.includes(commit.author.avatar_url)" :src="commit.author.avatar_url" alt="" class="size-4 rounded-full" loading="lazy" decoding="async" referrerpolicy="no-referrer" @error="avatarFailed(commit.author.avatar_url)">
                  {{ commit.author.name }}
                </component>
                <time :datetime="commit.committed_at" class="mono text-faint">{{ fmtTime(commit.committed_at) }}</time>
              </div>
              <details v-if="commit.body" class="group mt-1.5">
                <summary class="inline-flex min-h-11 cursor-pointer list-none items-center gap-1.5 text-xs text-muted transition-colors hover:text-ink [&::-webkit-details-marker]:hidden"><ChevronDown :size="13" class="transition-transform group-open:rotate-180" aria-hidden="true" />展开详情</summary>
                <pre class="mt-1 whitespace-pre-wrap break-words rounded-xl border border-line-soft bg-canvas/60 p-3 font-sans text-[13px] leading-relaxed text-ink-2">{{ commit.body }}</pre>
              </details>
            </div>
          </li>
        </ol>
        <p v-else class="px-5 py-10 text-center text-sm text-muted">暂时没有可展示的提交记录。</p>
        <a :href="commitsUrl" target="_blank" rel="noopener noreferrer" class="flex min-h-12 items-center justify-center gap-1.5 border-t border-line-soft text-sm text-muted transition-colors hover:bg-chip hover:text-ink">查看完整提交记录 <ArrowUpRight :size="14" aria-hidden="true" /></a>
      </section>

      <aside class="space-y-5">
        <section v-if="data?.release" class="panel p-5" aria-labelledby="release-title">
          <h2 id="release-title" class="flex items-center gap-2 text-sm font-bold text-ink"><Tag :size="16" class="text-brand" aria-hidden="true" /> 最新发布</h2>
          <a :href="data.release.url" target="_blank" rel="noopener noreferrer" class="mt-3 block break-words text-lg font-bold text-ink hover:text-brand-ink">{{ data.release.name }}</a>
          <p class="mono mt-1 text-xs text-muted">{{ data.release.tag }} · {{ fmtTime(data.release.published_at) }}</p>
          <details v-if="data.release.body" class="group mt-2">
            <summary class="inline-flex min-h-11 cursor-pointer items-center gap-1.5 text-xs text-muted hover:text-ink">发布说明<ChevronDown :size="13" class="transition-transform group-open:rotate-180" aria-hidden="true" /></summary>
            <pre class="whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed text-ink-2">{{ data.release.body }}</pre>
          </details>
        </section>

        <section class="panel overflow-hidden" aria-labelledby="contributors-title">
          <div class="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
            <h2 id="contributors-title" class="flex items-center gap-2 text-sm font-bold text-ink"><Users :size="17" class="text-brand" aria-hidden="true" /> 贡献者</h2>
            <span v-if="contributors.length" class="num text-xs text-muted">{{ contributors.length }} 位{{ data?.contributors_has_more ? '以上' : '' }}</span>
          </div>
          <ul v-if="contributors.length" class="divide-y divide-line-soft">
            <li v-for="person in contributors" :key="person.name">
              <a :href="person.url || undefined" target="_blank" rel="noopener noreferrer" class="flex min-h-20 items-center gap-3 px-5 py-3 transition-colors hover:bg-chip">
                <span class="relative grid size-11 shrink-0 place-items-center overflow-hidden rounded-full bg-chip font-bold text-ink-2" aria-hidden="true">
                  {{ person.name.slice(0, 1).toUpperCase() }}
                  <img v-if="person.avatar_url && !failedAvatars.includes(person.avatar_url)" :src="person.avatar_url" alt="" class="absolute inset-0 size-full object-cover" loading="lazy" decoding="async" referrerpolicy="no-referrer" @error="avatarFailed(person.avatar_url)">
                </span>
                <span class="min-w-0 flex-1"><span class="block truncate text-sm font-semibold text-ink">{{ person.name }}</span><span class="mt-1 block text-xs text-muted">{{ count(person.contributions) }} 次提交</span></span>
                <ArrowUpRight :size="15" class="shrink-0 text-faint" aria-hidden="true" />
              </a>
            </li>
          </ul>
          <p v-else class="px-5 py-7 text-sm text-muted">{{ status === 'pending' ? '正在读取贡献记录…' : '贡献记录暂时不可用。' }}</p>
          <a :href="`${repoUrl}/graphs/contributors`" target="_blank" rel="noopener noreferrer" class="flex min-h-12 items-center justify-center gap-1.5 border-t border-line-soft text-xs text-muted transition-colors hover:bg-chip hover:text-ink">在 GitHub 查看全部 <ArrowUpRight :size="13" aria-hidden="true" /></a>
        </section>

        <section class="panel p-5">
          <h2 class="text-sm font-bold text-ink">一起完善 AniSignal</h2>
          <p class="mt-2 text-[13px] leading-relaxed text-muted">反馈问题、提出建议，或提交代码。每一份贡献都能在这里留下记录。</p>
          <div class="mt-4 grid grid-cols-2 gap-2">
            <a :href="`${repoUrl}/issues`" target="_blank" rel="noopener noreferrer" class="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-[10px] border border-line text-xs font-medium text-ink-2 transition-colors hover:bg-chip"><MessageSquare :size="14" aria-hidden="true" /> 反馈问题</a>
            <a :href="`${repoUrl}/pulls`" target="_blank" rel="noopener noreferrer" class="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-[10px] border border-line text-xs font-medium text-ink-2 transition-colors hover:bg-chip"><GitBranch :size="14" aria-hidden="true" /> 参与贡献</a>
          </div>
        </section>
        <p v-if="data?.fetched_at" class="px-1 text-[11px] leading-relaxed text-faint">数据同步于 <time :datetime="data.fetched_at" class="mono">{{ fmtTime(data.fetched_at) }}</time></p>
      </aside>
    </div>
  </div>
</template>
