import { useChatAiAccess } from '@/pages/chat/lib/use-chat-ai-access'
import type { AiQuota, ChatAiAccessData } from '@/pages/chat/model/ai-access'
import {
  automaticSummaryLabels,
  purchaseView,
  quotaView,
  tierView,
} from '@/pages/chat/lib/ai-access-view'

import './selara-ai-panel.css'

function QuotaRow({ label, quota, periodLabel, timeZone }: {
  label: string
  quota: AiQuota
  periodLabel: string
  timeZone: string
}) {
  const view = quotaView(quota, periodLabel, timeZone)
  return (
    <div className={`selara-ai__row is-${view.tone}`}>
      <dt>{label}</dt>
      <dd>
        <strong>{view.headline}</strong>
        {view.percentUsed !== null && (
          <span
            className="selara-ai__bar"
            role="progressbar"
            aria-label={`${label}: использовано ${view.percentUsed}%`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={view.percentUsed}
          >
            <i style={{ width: `${view.percentUsed}%` }} />
          </span>
        )}
        {view.detail && <small>{view.detail}</small>}
      </dd>
    </div>
  )
}

function PanelBody({ data }: { data: ChatAiAccessData }) {
  const tier = tierView(data)
  const purchase = purchaseView(data)
  const available = data.state === 'available'
  return (
    <>
      <div className="selara-ai__status">
        <span className={`selara-ai__badge is-${tier.tone}`}>{tier.badge}</span>
        {tier.note && <span className="selara-ai__note">{tier.note}</span>}
      </div>
      {available && (
        <dl className="selara-ai__list">
          <QuotaRow label="AI-запросы" quota={data.llm} periodLabel="сегодня" timeZone={data.timezone} />
          <QuotaRow label="Ручные итоги" quota={data.manual_summary} periodLabel="в этом месяце" timeZone={data.timezone} />
          <div className="selara-ai__row">
            <dt>Автоматические итоги</dt>
            <dd><strong>{automaticSummaryLabels[data.automatic_summary.state]}</strong></dd>
          </div>
        </dl>
      )}
      {!available && (
        <p className="selara-ai__note">
          Лимиты и автоматические итоги временно недоступны для проверки. Это не означает, что доступ отключён.
        </p>
      )}
      {available && data.tier !== 'owner_internal' && (
        <p className="selara-ai__note">Selara AI открывает автоматические ежедневные итоги группы.</p>
      )}
      {purchase && 'label' in purchase && (
        <div className="selara-ai__cta">
          <a className="button button--primary" href={data.purchase.bot_dm_url} target="_blank" rel="noreferrer">
            {purchase.label}
          </a>
          <small>Оплата в личке с ботом: откройте {data.purchase.command} и выберите эту группу.</small>
        </div>
      )}
      {purchase && 'hint' in purchase && <p className="selara-ai__note">{purchase.hint}</p>}
    </>
  )
}

export function SelaraAiPanel({ chatId }: { chatId: string }) {
  const query = useChatAiAccess(chatId)

  return (
    <section className="miniapp-section-card selara-ai" aria-labelledby="selara-ai-title" aria-busy={query.isFetching}>
      <div className="miniapp-section-head">
        <div>
          <h2 id="selara-ai-title">Selara AI</h2>
        </div>
        {query.data && (
          <button className="button button--secondary" type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>
            {query.isFetching ? 'Обновляю…' : 'Обновить'}
          </button>
        )}
      </div>
      {query.isPending ? (
        <div className="selara-ai__skeleton" aria-label="Загрузка статуса Selara AI">
          <i /><i /><i />
        </div>
      ) : query.isError && !query.data ? (
        <div className="selara-ai__error" role="alert">
          <span>{query.error.message}</span>
          <button className="button button--secondary" type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>
            Повторить
          </button>
        </div>
      ) : query.data ? (
        <>
          <PanelBody data={query.data} />
          {query.isError && <p className="selara-ai__note">Не удалось обновить. Показаны последние данные.</p>}
        </>
      ) : null}
    </section>
  )
}
