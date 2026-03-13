import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { FamilyPageData } from '@/pages/family/model/types'
import { routes } from '@/shared/config/routes'
import { ChatSectionNav } from '@/shared/ui/chat-section-nav/ChatSectionNav'

import './family-page.css'

type FamilyPageViewProps = {
  chatId: string
  data: FamilyPageData
}

const SCENE_WIDTH = 980
const SCENE_HEIGHT = 620
const ROLE_ORDER = [
  'grandparent',
  'parent',
  'step_parent',
  'sibling',
  'subject',
  'spouse',
  'child',
  'pet',
  'relative',
] as const

function roleLabel(role: string) {
  if (role === 'subject') {
    return 'центр'
  }
  if (role === 'spouse') {
    return 'супруг(а)'
  }
  if (role === 'parent') {
    return 'родитель'
  }
  if (role === 'grandparent') {
    return 'дедушка или бабушка'
  }
  if (role === 'step_parent') {
    return 'приёмный родитель'
  }
  if (role === 'sibling') {
    return 'брат или сестра'
  }
  if (role === 'child') {
    return 'ребёнок'
  }
  if (role === 'pet') {
    return 'питомец'
  }
  return 'родственник'
}

function relationTypeLabel(value: string) {
  if (value === 'spouse') {
    return 'супруг(а)'
  }
  if (value === 'parent') {
    return 'родитель'
  }
  if (value === 'grandparent') {
    return 'дедушка или бабушка'
  }
  if (value === 'step_parent') {
    return 'приёмный родитель'
  }
  if (value === 'sibling') {
    return 'брат или сестра'
  }
  if (value === 'child') {
    return 'ребёнок'
  }
  if (value === 'pet') {
    return 'питомец'
  }
  return 'родственная связь'
}

export function FamilyPageView({ chatId, data }: FamilyPageViewProps) {
  const [selectedNodeId, setSelectedNodeId] = useState<number>(data.focus_user_id)
  const selectedNode = useMemo(
    () => data.family_nodes.find((node) => node.id === selectedNodeId) ?? data.family_nodes[0] ?? null,
    [data.family_nodes, selectedNodeId],
  )
  const positionMap = useMemo(() => {
    const grouped = new Map<string, typeof data.family_nodes>()
    for (const role of ROLE_ORDER) {
      grouped.set(role, [])
    }

    for (const node of data.family_nodes) {
      const role = grouped.has(node.role) ? node.role : 'relative'
      grouped.set(role, [...(grouped.get(role) ?? []), node])
    }

    const positions = new Map<number, { x: number; y: number }>()
    const placeRow = (
      items: typeof data.family_nodes,
      y: number,
      startX: number,
      stepX: number,
    ) => {
      const centered = startX - (Math.max(0, items.length - 1) * stepX) / 2
      items.forEach((node, index) => {
        positions.set(node.id, { x: centered + index * stepX, y })
      })
    }

    placeRow(grouped.get('grandparent') ?? [], 86, SCENE_WIDTH / 2, 180)
    placeRow(
      [...(grouped.get('parent') ?? []), ...(grouped.get('step_parent') ?? [])],
      190,
      SCENE_WIDTH / 2,
      170,
    )
    placeRow([...(grouped.get('sibling') ?? [])], 308, SCENE_WIDTH / 2 - 260, 150)
    placeRow(
      [...(grouped.get('subject') ?? []), ...(grouped.get('spouse') ?? [])],
      308,
      SCENE_WIDTH / 2 + 90,
      190,
    )
    placeRow([...(grouped.get('child') ?? [])], 442, SCENE_WIDTH / 2, 170)
    placeRow([...(grouped.get('pet') ?? [])], 540, SCENE_WIDTH / 2, 150)
    placeRow([...(grouped.get('relative') ?? [])], 308, SCENE_WIDTH / 2, 150)

    return positions
  }, [data])
  const relatedRows = useMemo(() => {
    if (!selectedNode) {
      return []
    }

    const nodeMap = new Map(data.family_nodes.map((node) => [node.id, node]))
    const related = data.family_edges.filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id)

    return related.map((edge) => {
      const otherId = edge.source === selectedNode.id ? edge.target : edge.source
      const otherNode = nodeMap.get(otherId)
      return {
        id: otherId,
        label: otherNode?.label ?? `user:${otherId}`,
        edgeLabel: edge.label,
        relationType: relationTypeLabel(edge.relation_type),
        href: otherNode?.href ?? `${routes.family(chatId)}?user_id=${otherId}`,
      }
    })
  }, [chatId, data.family_edges, data.family_nodes, selectedNode])

  return (
    <div className="family-page">
      <section className="family-hero">
        <div>
          <span className="page-card__eyebrow">Моя семья</span>
          <h1>{data.chat_title}</h1>
          <p>Интерактивное дерево отношений вокруг выбранного участника.</p>
        </div>
        <div className="family-hero__chips">
          <span className="family-chip">Фокус: {data.focus_user_id}</span>
          <span className="family-chip">{data.focus_label}</span>
          <span className="family-chip">Семейный граф</span>
          <Link className="button" to={routes.chat(chatId)}>
            К группе
          </Link>
        </div>
      </section>

      <ChatSectionNav links={data.chat_section_links} />

      <section className="family-metrics">
        {data.bundle_summary.map((item) => (
          <article key={item.label} className="family-metric">
            <span className="family-metric__label">{item.label}</span>
            <strong className="family-metric__value">{item.value}</strong>
            <span className="family-metric__note">текущее окно вокруг фокуса</span>
          </article>
        ))}
      </section>

      <section className="family-grid">
        <article className="family-panel family-scene-panel">
          <div className="family-panel__head">
            <div>
              <h2>Граф связей</h2>
              <p>Наведение обновляет правую панель, переход по имени меняет центр дерева.</p>
            </div>
          </div>
          <div className="family-scene">
            <svg
              className="family-scene__svg"
              viewBox={`0 0 ${SCENE_WIDTH} ${SCENE_HEIGHT}`}
              aria-hidden="true"
            >
              {data.family_edges.map((edge) => {
                const source = positionMap.get(edge.source)
                const target = positionMap.get(edge.target)

                if (!source || !target) {
                  return null
                }

                return (
                  <line
                    key={`${edge.source}-${edge.target}-${edge.relation_type}`}
                    className={`family-edge family-edge--${edge.relation_type}`}
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                    strokeDasharray={edge.is_direct ? undefined : '8 6'}
                  />
                )
              })}
            </svg>

            {data.family_nodes.map((node) => {
              const position = positionMap.get(node.id)

              if (!position) {
                return null
              }

              return (
                <Link
                  key={node.id}
                  to={node.href}
                  className={
                    selectedNode?.id === node.id
                      ? `family-scene-node family-scene-node--${node.role} is-active`
                      : `family-scene-node family-scene-node--${node.role}`
                  }
                  style={{ left: `${position.x}px`, top: `${position.y}px` }}
                  onMouseEnter={() => setSelectedNodeId(node.id)}
                  onFocus={() => setSelectedNodeId(node.id)}
                >
                  <strong>{node.label}</strong>
                  <span>#{node.id}</span>
                  <small>{roleLabel(node.role)}</small>
                </Link>
              )
            })}
          </div>
        </article>

        <article className="family-panel">
          <div className="family-panel__head">
            <div>
              <h2>Связи узла</h2>
              <p>Роли и связи относительно текущего выбранного участника.</p>
            </div>
          </div>
          {selectedNode ? (
            <div className="family-related-list">
              <div className="family-related-row">
                <div>
                  <strong>{selectedNode.label}</strong>
                  <p>Роль: {roleLabel(selectedNode.role)}</p>
                </div>
                <span>#{selectedNode.id}</span>
              </div>

              {relatedRows.length > 0 ? (
                relatedRows.map((row) => (
                  <Link key={`${selectedNode.id}-${row.id}-${row.relationType}`} className="family-related-row" to={row.href}>
                    <div>
                      <strong>{row.label}</strong>
                      <p>{row.edgeLabel}</p>
                    </div>
                    <span>{row.relationType}</span>
                  </Link>
                ))
              ) : (
                <p className="family-empty">Для этого узла связи не найдены.</p>
              )}
            </div>
          ) : (
            <p className="family-empty">Узлы графа не найдены.</p>
          )}
        </article>
      </section>

      <section className="family-panel">
        <div className="family-panel__head">
          <div>
            <h2>Участники в графе</h2>
            <p>Быстрый переход на другой центр дерева.</p>
          </div>
        </div>
        <div className="family-token-cloud">
          {data.family_nodes.map((node) => (
            <Link key={`${node.id}-token`} className="family-token-chip" to={node.href}>
              {node.label}
            </Link>
          ))}
        </div>
      </section>
    </div>
  )
}
