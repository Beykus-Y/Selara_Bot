import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { EconomyPageData, EconomyTradePoint } from '@/pages/economy/model/types'
import { routes } from '@/shared/config/routes'
import { ChatSectionNav } from '@/shared/ui/chat-section-nav/ChatSectionNav'

import './economy-page.css'

type EconomyPageViewProps = {
  chatId: string
  data: EconomyPageData
  feedbackMessage: string | null
  isMutating: boolean
  onApplyItem: (payload: { item_code: string; target_type: string; plot_no?: number | null }) => Promise<void>
  onCreateListing: (payload: { item_code: string; quantity: number; unit_price: number }) => Promise<void>
  onBuyListing: (payload: { listing_id: number; quantity: number }) => Promise<void>
  onCancelListing: (listingId: number) => Promise<void>
}

function latestTradeLabel(points: EconomyTradePoint[]) {
  if (points.length === 0) {
    return 'Сделок нет'
  }

  const latest = points[points.length - 1]
  return `${latest.unit_price} за шт. • ${latest.when}`
}

function filterGroupLabel(value: string) {
  if (value === 'seeds') {
    return 'Семена'
  }

  if (value === 'consumables') {
    return 'Расходники'
  }

  return 'Все товары'
}

function buildPolyline(points: EconomyTradePoint[]) {
  if (points.length === 0) {
    return { polyline: '', markers: [] as Array<{ x: number; y: number; value: number }> }
  }

  const width = 320
  const height = 160
  const padding = 18
  const prices = points.map((point) => point.unit_price)
  const minPrice = Math.min(...prices)
  const maxPrice = Math.max(...prices)
  const range = Math.max(1, maxPrice - minPrice)

  const markers = points.map((point, index) => {
    const x =
      points.length === 1
        ? width / 2
        : padding + ((width - padding * 2) / (points.length - 1)) * index
    const y =
      height -
      padding -
      ((point.unit_price - minPrice) / range) * (height - padding * 2)

    return {
      x: Math.round(x * 100) / 100,
      y: Math.round(y * 100) / 100,
      value: point.unit_price,
    }
  })

  return {
    polyline: markers.map((point) => `${point.x},${point.y}`).join(' '),
    markers,
  }
}

export function EconomyPageView({
  chatId,
  data,
  feedbackMessage,
  isMutating,
  onApplyItem,
  onCreateListing,
  onBuyListing,
  onCancelListing,
}: EconomyPageViewProps) {
  const tradeEntries = Object.entries(data.trade_points)
  const itemLabelMap = useMemo(
    () => {
      const labels = new Map<string, string>()

      for (const item of data.inventory_items) {
        labels.set(item.item_code, item.label)
      }

      for (const row of data.market_rows) {
        labels.set(row.item_code, row.label)
      }

      return labels
    },
    [data.inventory_items, data.market_rows],
  )
  const [selectedItemCode, setSelectedItemCode] = useState<string | null>(null)
  const [buyQuantities, setBuyQuantities] = useState<Record<number, string>>({})
  const [marketFilter, setMarketFilter] = useState<'all' | 'seeds' | 'consumables'>('all')
  const [marketSort, setMarketSort] = useState<'asc' | 'desc' | null>(null)
  const [selectedTradeItemCode, setSelectedTradeItemCode] = useState(tradeEntries[0]?.[0] ?? '')
  const [listingDraft, setListingDraft] = useState({
    item_code: data.inventory_items[0]?.item_code ?? '',
    quantity: '1',
    unit_price: '1',
  })
  const selectedItem = useMemo(
    () => data.inventory_items.find((item) => item.item_code === selectedItemCode) ?? null,
    [data.inventory_items, selectedItemCode],
  )
  const visibleMarketRows = useMemo(() => {
    const filtered = data.market_rows.filter((row) => {
      if (marketFilter === 'all') {
        return true
      }

      return row.filter_group === marketFilter || row.filter_group === 'all'
    })

    if (!marketSort) {
      return filtered
    }

    return [...filtered].sort((left, right) =>
      marketSort === 'asc' ? left.unit_price - right.unit_price : right.unit_price - left.unit_price,
    )
  }, [data.market_rows, marketFilter, marketSort])
  const activeTradeItemCode =
    selectedTradeItemCode && data.trade_points[selectedTradeItemCode]
      ? selectedTradeItemCode
      : tradeEntries[0]?.[0] ?? ''
  const activeListingItemCode =
    listingDraft.item_code && data.inventory_items.some((item) => item.item_code === listingDraft.item_code)
      ? listingDraft.item_code
      : data.inventory_items[0]?.item_code ?? ''
  const selectedTradePoints = data.trade_points[activeTradeItemCode] ?? []
  const chart = buildPolyline(selectedTradePoints)

  return (
    <div className="economy-page">
      <section className="economy-hero">
        <div>
          <span className="page-card__eyebrow">Экономика группы</span>
          <h1>{data.chat_title}</h1>
          <p>Ферма, инвентарь и рынок работают прямо в браузере: можно перетаскивать предметы, фильтровать лоты и смотреть историю цен.</p>
        </div>
        <div className="economy-hero__chips">
          <span className="economy-chip">Контур: {data.scope_id}</span>
          <span className="economy-chip">Баланс: {data.dashboard.balance}</span>
          <span className="economy-chip">Последняя культура: {data.last_crop_label}</span>
          <Link className="button" to={routes.chat(chatId)}>
            К группе
          </Link>
        </div>
      </section>

      <ChatSectionNav links={data.chat_section_links} />

      <section className="economy-metrics">
        <article className="economy-metric economy-metric--violet">
          <span className="economy-metric__label">Баланс</span>
          <strong className="economy-metric__value">{data.dashboard.balance}</strong>
          <span className="economy-metric__note">{data.scope_id}</span>
        </article>
        <article className="economy-metric economy-metric--cyan">
          <span className="economy-metric__label">Ферма</span>
          <strong className="economy-metric__value">ур. {data.dashboard.farm_level}</strong>
          <span className="economy-metric__note">размер: {data.dashboard.farm_size_tier}</span>
        </article>
        <article className="economy-metric economy-metric--magenta">
          <span className="economy-metric__label">Рост</span>
          <strong className="economy-metric__value">{data.dashboard.growth_size_mm} мм</strong>
          <span className="economy-metric__note">действий: {data.dashboard.growth_actions}</span>
        </article>
        <article className="economy-metric economy-metric--indigo">
          <span className="economy-metric__label">Инвентарь</span>
          <strong className="economy-metric__value">{data.inventory_items.length}</strong>
          <span className="economy-metric__note">уникальных предметов</span>
        </article>
      </section>

      {feedbackMessage ? <section className="economy-banner">{feedbackMessage}</section> : null}

      <section className="economy-grid">
        <article className="economy-panel">
          <div className="economy-panel__head">
            <div>
              <h2>Ферма</h2>
              <p>Перетащите семена на пустую грядку, а расходники на занятую. На телефоне сначала выберите предмет, затем цель.</p>
            </div>
          </div>
          <div className="economy-plot-grid">
            {data.plot_cards.map((plot) => {
              const targetType = plot.state === 'empty' ? 'plot-empty' : 'plot-occupied'
              const isReadyTarget = selectedItem?.target === targetType

              return (
                <button
                  key={plot.plot_no}
                  type="button"
                  className={
                    isReadyTarget
                      ? `economy-plot-card economy-plot-card--${plot.state} is-drop-target`
                      : `economy-plot-card economy-plot-card--${plot.state}`
                  }
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault()
                    const itemCode = event.dataTransfer.getData('text/item-code')
                    const itemTarget = event.dataTransfer.getData('text/item-target')

                    if (!itemCode || itemTarget !== targetType) {
                      return
                    }

                    void onApplyItem({
                      item_code: itemCode,
                      target_type: targetType,
                      plot_no: plot.plot_no,
                    })
                  }}
                  onClick={() => {
                    if (!selectedItem || selectedItem.target !== targetType) {
                      return
                    }

                    void onApplyItem({
                      item_code: selectedItem.item_code,
                      target_type: targetType,
                      plot_no: plot.plot_no,
                    })
                  }}
                >
                  <span className="economy-plot-card__no">#{plot.plot_no}</span>
                  <strong>{plot.crop_label}</strong>
                  <span>{plot.note}</span>
                </button>
              )
            })}
          </div>
        </article>

        <article className="economy-panel">
          <div className="economy-panel__head">
            <div>
              <h2>Инвентарь</h2>
              <p>Предмет можно перетащить на цель или выбрать карточку и нажать по нужной области.</p>
            </div>
          </div>
          <div className="economy-inventory-grid">
            {data.inventory_items.map((item) => (
              <button
                key={item.item_code}
                className={selectedItemCode === item.item_code ? 'economy-inventory-card is-selected' : 'economy-inventory-card'}
                type="button"
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData('text/item-code', item.item_code)
                  event.dataTransfer.setData('text/item-target', item.target)
                }}
                onClick={() => {
                  setSelectedItemCode((current) => (current === item.item_code ? null : item.item_code))
                }}
              >
                <strong>{item.label}</strong>
                <span>{item.quantity} шт.</span>
                <small>
                  {item.target === 'self'
                    ? 'Применяется к персонажу'
                    : item.target === 'plot-empty'
                      ? 'Нужна пустая грядка'
                      : 'Нужна занятая грядка'}
                </small>
              </button>
            ))}
          </div>
          <button
            type="button"
            className={selectedItem?.target === 'self' ? 'economy-self-card is-drop-target' : 'economy-self-card'}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault()
              const itemCode = event.dataTransfer.getData('text/item-code')
              const itemTarget = event.dataTransfer.getData('text/item-target')

              if (!itemCode || itemTarget !== 'self') {
                return
              }

              void onApplyItem({
                item_code: itemCode,
                target_type: 'self',
              })
            }}
            onClick={() => {
              if (!selectedItem || selectedItem.target !== 'self') {
                return
              }

              void onApplyItem({
                item_code: selectedItem.item_code,
                target_type: 'self',
              })
            }}
          >
            <strong>Персонаж</strong>
            <span>Сюда применяются предметы личного использования.</span>
          </button>
        </article>
      </section>

      <section className="economy-grid">
        <article className="economy-panel">
          <div className="economy-panel__head">
            <div>
              <h2>Рынок</h2>
              <p>Создание, покупка и снятие лотов работают прямо из браузера, с фильтрацией и сортировкой по цене.</p>
            </div>
          </div>

          <form
            className="economy-market-create"
            onSubmit={(event) => {
              event.preventDefault()
              void onCreateListing({
                item_code: activeListingItemCode,
                quantity: Number(listingDraft.quantity || 0),
                unit_price: Number(listingDraft.unit_price || 0),
              })
            }}
          >
            <select
              value={activeListingItemCode}
              onChange={(event) => setListingDraft((current) => ({ ...current, item_code: event.target.value }))}
            >
              {data.inventory_items.map((item) => (
                <option key={`listing-${item.item_code}`} value={item.item_code}>
                  {item.label} ({item.quantity})
                </option>
              ))}
            </select>
            <input
              type="number"
              min="1"
              value={listingDraft.quantity}
              onChange={(event) => setListingDraft((current) => ({ ...current, quantity: event.target.value }))}
              placeholder="Количество"
            />
            <input
              type="number"
              min="1"
              value={listingDraft.unit_price}
              onChange={(event) => setListingDraft((current) => ({ ...current, unit_price: event.target.value }))}
              placeholder="Цена за штуку"
            />
            <button className="button" type="submit" disabled={isMutating || !activeListingItemCode}>
              Выставить лот
            </button>
          </form>

          <div className="economy-market-toolbar">
            <div className="economy-market-filter">
              <button
                className={marketFilter === 'all' ? 'button button--primary' : 'button'}
                type="button"
                onClick={() => setMarketFilter('all')}
              >
                Все
              </button>
              <button
                className={marketFilter === 'seeds' ? 'button button--primary' : 'button'}
                type="button"
                onClick={() => setMarketFilter('seeds')}
              >
                Семена
              </button>
              <button
                className={marketFilter === 'consumables' ? 'button button--primary' : 'button'}
                type="button"
                onClick={() => setMarketFilter('consumables')}
              >
                Расходники
              </button>
            </div>

            <div className="economy-market-filter">
              <button
                className={marketSort === 'asc' ? 'button button--primary' : 'button'}
                type="button"
                onClick={() => setMarketSort('asc')}
              >
                Цена по возрастанию
              </button>
              <button
                className={marketSort === 'desc' ? 'button button--primary' : 'button'}
                type="button"
                onClick={() => setMarketSort('desc')}
              >
                Цена по убыванию
              </button>
            </div>
          </div>

          <div className="economy-market-grid">
            {visibleMarketRows.length > 0 ? (
              visibleMarketRows.map((row) => (
                <article key={row.id} className="economy-market-row">
                  <div className="economy-market-row__head">
                    <strong>{row.label}</strong>
                    <code>#{row.id}</code>
                  </div>
                  <p>
                    Осталось {row.qty_left}/{row.qty_total} • {row.unit_price} за шт.
                  </p>
                  <small>
                    {row.is_own ? 'Ваш лот' : `Продавец: ${row.seller_label}`} • {filterGroupLabel(row.filter_group)}
                  </small>
                  {row.is_own ? (
                    <button
                      className="button button--danger"
                      type="button"
                      disabled={isMutating}
                      onClick={() => {
                        void onCancelListing(row.id)
                      }}
                    >
                      Снять лот
                    </button>
                  ) : (
                    <form
                      className="economy-inline-form"
                      onSubmit={(event) => {
                        event.preventDefault()
                        void onBuyListing({
                          listing_id: row.id,
                          quantity: Number(buyQuantities[row.id] || '1'),
                        })
                      }}
                    >
                      <input
                        type="number"
                        min="1"
                        max={row.qty_left}
                        value={buyQuantities[row.id] ?? '1'}
                        onChange={(event) =>
                          setBuyQuantities((current) => ({ ...current, [row.id]: event.target.value }))
                        }
                      />
                      <button className="button" type="submit" disabled={isMutating}>
                        Купить
                      </button>
                    </form>
                  )}
                </article>
              ))
            ) : (
              <p className="economy-empty">По выбранному фильтру предложений пока нет.</p>
            )}
          </div>
        </article>

        <article className="economy-panel">
          <div className="economy-panel__head">
            <div>
              <h2>История цен</h2>
              <p>Сделки за последние семь дней по выбранному товару.</p>
            </div>
          </div>

          {tradeEntries.length > 0 ? (
            <>
              <select
                className="economy-chart-select"
                value={activeTradeItemCode}
                onChange={(event) => setSelectedTradeItemCode(event.target.value)}
              >
                {tradeEntries.map(([itemCode]) => (
                  <option key={itemCode} value={itemCode}>
                    {itemLabelMap.get(itemCode) ?? itemCode}
                  </option>
                ))}
              </select>

              {selectedTradePoints.length > 0 ? (
                <>
                  <div className="economy-chart-card">
                    <svg className="economy-chart" viewBox="0 0 320 160" role="img" aria-label="График цены">
                      <rect x="0" y="0" width="320" height="160" rx="22" />
                      <polyline points={chart.polyline} />
                      {chart.markers.map((marker, index) => (
                        <circle key={`${marker.x}-${index}`} cx={marker.x} cy={marker.y} r="4" />
                      ))}
                    </svg>
                  </div>
                  <div className="economy-trades-list">
                    {selectedTradePoints.map((point, index) => (
                      <div key={`${point.when}-${index}`} className="economy-trade-card">
                        <strong>{point.when}</strong>
                        <p>{point.unit_price} за шт.</p>
                        <small>
                          Количество: {point.quantity} • Сумма: {point.total_price}
                        </small>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <p className="economy-empty">По выбранному товару сделок пока не было.</p>
              )}
            </>
          ) : (
            <p className="economy-empty">История цен пока пуста.</p>
          )}
        </article>
      </section>

      <section className="economy-panel">
        <div className="economy-panel__head">
          <div>
            <h2>Короткая сводка по рынку</h2>
            <p>Последняя цена и число сделок по каждому товару.</p>
          </div>
        </div>
        <div className="economy-trades-list">
          {tradeEntries.length > 0 ? (
            tradeEntries.map(([itemCode, points]) => (
              <div key={itemCode} className="economy-trade-card">
                <strong>{itemLabelMap.get(itemCode) ?? itemCode}</strong>
                <p>{latestTradeLabel(points)}</p>
                <small>Сделок: {points.length}</small>
              </div>
            ))
          ) : (
            <p className="economy-empty">История цен пока пуста.</p>
          )}
        </div>
      </section>
    </div>
  )
}
