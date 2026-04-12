import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { adminTableUpdate } from '@/pages/admin-table/api/admin-table-api'
import type { AdminTableEditPageData } from '@/pages/admin-table/model/types'
import { routes } from '@/shared/config/routes'

import './admin-table-page.css'

type AdminTableEditPageViewProps = {
  data: AdminTableEditPageData
}

export function AdminTableEditPageView({ data }: AdminTableEditPageViewProps) {
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  const initialValues: Record<string, string> = {}
  for (const col of data.columns) {
    if (col.type === 'datetime') {
      initialValues[col.name] = col.value != null ? String(col.value) : ''
    } else if (col.type === 'bool') {
      initialValues[col.name] = col.value ? 'true' : 'false'
    } else {
      initialValues[col.name] = col.value != null ? String(col.value) : ''
    }
  }

  const [fields, setFields] = useState<Record<string, string>>(initialValues)

  const updateMutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, string> = {}
      // include pk fields
      for (const [k, v] of data.pk_fields) {
        payload[String(k)] = String(v)
      }
      // include editable fields
      for (const col of data.columns) {
        if (!col.is_pk) {
          payload[col.name] = fields[col.name] ?? ''
        }
      }
      return adminTableUpdate(data.table_name, payload)
    },
    onSuccess: () => {
      void navigate(routes.adminTable(data.table_name))
    },
    onError: (err: Error) => setError(err.message),
  })

  return (
    <div className="admin-table-page">
      {error && <div className="admin-error">{error}</div>}

      <article className="admin-table-panel">
        <div className="admin-table-panel__head">
          <div>
            <nav className="admin-breadcrumb">
              <Link to={routes.admin}>Админка</Link>
              <span className="admin-breadcrumb-sep">/</span>
              <Link to={routes.adminTable(data.table_name)}>{data.table_title}</Link>
              <span className="admin-breadcrumb-sep">/</span>
              <span>Редактирование</span>
            </nav>
            <h2>Редактирование записи</h2>
            <p className="admin-table-subtitle">
              Таблица: <code>{data.table_name}</code> · ID: <code>{data.record_id}</code>
            </p>
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            updateMutation.mutate()
          }}
          style={{ display: 'grid', gap: '1rem', maxWidth: '600px' }}
        >
          {data.columns.map((col) => (
            <div key={col.name} style={{ display: 'grid', gap: '0.35rem' }}>
              <label style={{ fontSize: '0.85rem', fontWeight: 500, color: 'var(--text-muted)' }}>
                {col.name}
                {col.is_pk && <span style={{ marginLeft: '0.4rem', fontSize: '0.75rem', opacity: 0.6 }}>(PK)</span>}
              </label>

              {col.type === 'datetime' ? (
                <input
                  type="datetime-local"
                  className="admin-search-input"
                  style={{ width: '100%' }}
                  step="1"
                  value={fields[col.name]}
                  readOnly={col.is_pk}
                  onChange={(e) => !col.is_pk && setFields((p) => ({ ...p, [col.name]: e.target.value }))}
                />
              ) : col.type === 'bool' ? (
                <select
                  className="admin-search-input"
                  style={{ width: '100%' }}
                  value={fields[col.name]}
                  disabled={col.is_pk}
                  onChange={(e) => setFields((p) => ({ ...p, [col.name]: e.target.value }))}
                >
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : col.type === 'textarea' ? (
                <textarea
                  className="admin-search-input"
                  style={{ width: '100%', minHeight: '100px', resize: 'vertical', fontFamily: 'inherit' }}
                  value={fields[col.name]}
                  readOnly={col.is_pk}
                  onChange={(e) => !col.is_pk && setFields((p) => ({ ...p, [col.name]: e.target.value }))}
                />
              ) : (
                <input
                  type="text"
                  className="admin-search-input"
                  style={{ width: '100%' }}
                  value={fields[col.name]}
                  readOnly={col.is_pk}
                  onChange={(e) => !col.is_pk && setFields((p) => ({ ...p, [col.name]: e.target.value }))}
                />
              )}

              {col.ref_label && (
                <span className="text-muted">{col.ref_label}</span>
              )}
            </div>
          ))}

          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.5rem' }}>
            <Link className="button ghost" to={routes.adminTable(data.table_name)}>Отмена</Link>
            <button type="submit" className="button primary" disabled={updateMutation.isPending}>
              {updateMutation.isPending ? 'Сохраняю…' : 'Сохранить'}
            </button>
          </div>
        </form>

        <div className="admin-back-link">
          <Link className="button ghost" to={routes.adminTable(data.table_name)}>← Назад к таблице</Link>
        </div>
      </article>
    </div>
  )
}
