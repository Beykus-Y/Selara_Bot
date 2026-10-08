import type { MiniAppGroup } from '@/shared/miniapp/model'

export function groupRoleText(group: MiniAppGroup) {
  if (group.is_admin) return 'администратор'
  return 'участник'
}

export function groupLetter(group: MiniAppGroup) {
  // Iterate code points, not UTF-16 units, so a leading emoji is not split into a broken surrogate.
  const [first] = Array.from(group.title.trim())
  return first ? first.toUpperCase() : '?'
}

export function mergeGroups(...lists: MiniAppGroup[][]) {
  const map = new Map<number, MiniAppGroup>()
  lists.flat().forEach((group) => map.set(group.chat_id, group))
  return [...map.values()]
}
