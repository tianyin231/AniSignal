/** 头像预设库（与后端 app/services/avatars.py 同一份 key 白名单） */

export interface AvatarPreset {
  id: string
  label: string
  gradient: string
}

export const AVATARS: AvatarPreset[] = [
  { id: 'aurora-blue', label: '极光蓝', gradient: 'from-blue-400 to-blue-600' },
  { id: 'sky-cyan', label: '晴空', gradient: 'from-sky-400 to-cyan-500' },
  { id: 'teal-mint', label: '薄荷', gradient: 'from-teal-400 to-emerald-500' },
  { id: 'forest', label: '森绿', gradient: 'from-emerald-500 to-green-600' },
  { id: 'amber-sun', label: '暖阳', gradient: 'from-amber-400 to-orange-500' },
  { id: 'coral', label: '珊瑚', gradient: 'from-orange-400 to-rose-500' },
  { id: 'rose-petal', label: '蔷薇', gradient: 'from-rose-400 to-pink-500' },
  { id: 'crimson', label: '绯红', gradient: 'from-red-400 to-red-600' },
  { id: 'slate-stone', label: '岩灰', gradient: 'from-slate-400 to-slate-600' },
  { id: 'midnight', label: '夜航', gradient: 'from-slate-700 to-zinc-900' },
  { id: 'wave-navy', label: '浪涌', gradient: 'from-cyan-500 to-blue-700' },
  { id: 'meadow', label: '草甸', gradient: 'from-lime-400 to-teal-500' },
]

export const DEFAULT_AVATAR = 'aurora-blue'

export function avatarGradient(id: string | null | undefined): string {
  return AVATARS.find((a) => a.id === id)?.gradient ?? AVATARS[0]!.gradient
}

/** 展示名：昵称 > 邮箱前缀 */
export function displayName(user: { display_name?: string | null; email: string } | null | undefined): string {
  if (!user) return '…'
  return user.display_name?.trim() || user.email.split('@')[0]!
}
