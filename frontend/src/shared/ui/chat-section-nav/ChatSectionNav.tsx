import { Link } from 'react-router-dom'

import { buildChatSectionLinks, type ChatSection } from '@/shared/config/routes'

type ChatSectionNavProps = {
  chatId?: string | number
  active?: ChatSection
  canManageSettings?: boolean
  links?: Array<{
    href: string
    label: string
    variant: string
  }>
}

export function ChatSectionNav({
  chatId,
  active,
  canManageSettings = true,
  links,
}: ChatSectionNavProps) {
  const items =
    links ??
    (chatId !== undefined && active !== undefined
      ? buildChatSectionLinks(chatId, { active, canManageSettings }).map((item) => ({
          href: item.to,
          label: item.label,
          variant: item.isActive ? 'primary' : 'ghost',
        }))
      : [])

  if (items.length === 0) {
    return null
  }

  return (
    <nav className="context-tabs" aria-label="Разделы чата">
      {items.map((item) => (
        <Link
          key={`${item.href}-${item.label}`}
          className={item.variant === 'primary' ? 'context-tabs__link context-tabs__link--active' : 'context-tabs__link'}
          to={item.href}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  )
}
