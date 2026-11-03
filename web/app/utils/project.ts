export const PROJECT_REPOSITORY = 'tianyin231/AniSignal'
export const PROJECT_GITHUB_URL = `https://github.com/${PROJECT_REPOSITORY}`

export interface ProjectAuthor {
  name: string
  url: string | null
  avatar_url: string | null
}

export interface ProjectOverview {
  repository: {
    name: string
    full_name: string
    url: string
    description: string | null
    default_branch: string | null
    stars: number | null
    forks: number | null
  }
  commits: Array<{
    sha: string
    url: string
    title: string
    body: string | null
    committed_at: string
    author: ProjectAuthor
  }>
  contributors: Array<ProjectAuthor & { contributions: number }>
  contributors_has_more: boolean
  release: { name: string; tag: string; url: string; published_at: string; body: string | null } | null
  fetched_at: string | null
  stale: boolean
}
