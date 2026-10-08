import type { MiniAppGroup } from '@/shared/miniapp/model'

export function groupRoleText(group: MiniAppGroup) {
  if (group.badge === 'owner') return 'владелец'
  if (group.is_admin) return 'администратор'
  return 'участник'
}

export function groupLetter(group: MiniAppGroup) {
  return group.title.trim().slice(0, 1).toUpperCase() || '?'
}

export function isGroupLive(lastSeen: string) {
  return /минут|секунд|онлайн|online|сейчас/i.test(lastSeen)
}

export function mergeGroups(...lists: MiniAppGroup[][]) {
  const map = new Map<number, MiniAppGroup>()
  lists.flat().forEach((group) => map.set(group.chat_id, group))
  return [...map.values()]
}
