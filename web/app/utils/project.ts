export const PROJECT_REPOSITORY = 'tianyin231/AniSignal'
export const PROJECT_GITHUB_URL = `https://github.com/${PROJECT_REPOSITORY}`
export const PLUGIN_REPOSITORY = 'tianyin231/AstrBot_Plugin_AniSignal'

export type ProjectRepositoryKey = 'anisignal' | 'plugin'

/** GitHub 暂时读不到时，页面仍按这份清单展示两个仓库的入口。 */
export const PROJECT_REPOSITORIES: Array<{ key: ProjectRepositoryKey; full_name: string; role: string; short: string; summary: string }> = [
  { key: 'anisignal', full_name: PROJECT_REPOSITORY, role: '主站与服务端', short: '主站', summary: '网站、采集与推送服务，以及插件源码。' },
  { key: 'plugin', full_name: PLUGIN_REPOSITORY, role: 'AstrBot 插件', short: '插件', summary: '在 QQ 群聊和私聊接收早报、动态与提醒，从这里安装和更新。' },
]

export interface ProjectAuthor {
  name: string
  url: string | null
  avatar_url: string | null
}

export interface ProjectCommit {
  sha: string
  url: string
  title: string
  body: string | null
  committed_at: string
  author: ProjectAuthor
}

export interface ProjectRepository {
  key: ProjectRepositoryKey
  role: string
  name: string
  full_name: string
  url: string
  description: string | null
  default_branch: string | null
  stars: number | null
  forks: number | null
  /** 插件仓库读自 metadata.yaml；主仓库为 null，版本看 release。 */
  version: string | null
  release: { name: string; tag: string; url: string; published_at: string; body: string | null } | null
  commits: ProjectCommit[]
}

export interface ProjectOverview {
  repositories: ProjectRepository[]
  contributors: Array<ProjectAuthor & { contributions: number }>
  contributors_has_more: boolean
  fetched_at: string | null
  stale: boolean
}
