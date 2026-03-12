import { routes } from '@/shared/config/routes'
import { PagePlaceholder } from '@/shared/ui/PagePlaceholder'

type ChatTabPlaceholderProps = {
  chatId: string
  tab: 'achievements' | 'settings'
}

export function ChatTabPlaceholder({ chatId, tab }: ChatTabPlaceholderProps) {
  const isAchievements = tab === 'achievements'

  return (
    <div className="chat-tab-placeholder">
      <PagePlaceholder
        title={isAchievements ? `Достижения чата ${chatId}` : `Настройки чата ${chatId}`}
        description={
          isAchievements
            ? 'Следующим срезом сюда переедут локальные достижения группы.'
            : 'Следующим срезом сюда переедут настройки и управляющие формы.'
        }
        bullets={[
          'Обзор группы уже работает в новом веб-клиенте через существующие API.',
          'Этот раздел пока выделен в отдельный этап, чтобы не смешивать статистику и конфигурацию.',
          `Связанные разделы уже доступны: ${routes.economy(chatId)} и ${routes.family(chatId)}.`,
        ]}
      />
    </div>
  )
}
