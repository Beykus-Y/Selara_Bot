import { useState, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { adminTableDelete } from '@/pages/admin-table/api/admin-table-api'
import type { AdminTablePageData, TableRow } from '@/pages/admin-table/model/types'
import { routes } from '@/shared/config/routes'

import './admin-table-page.css'

type AdminTablePageViewProps = {
  tableName: string
  data: AdminTablePageData
  filters: Record<string, string>
  onFilter: (filters: Record<string, string>) => void
}

function renderCellValue(
  col: string,
  value: unknown,
  refLabel?: string,
): React.ReactNode {
  if (value === null || value === undefined) {
    return <span className="admin-null">NULL</span>
  }
  if (value === true) {
    return <span className="badge badge-ok">true</span>
  }
  if (value === false) {
    return <span className="badge badge-error">false</span>
  }
  const str = String(value)
  const display = str.length > 50 ? str.slice(0, 50) + '…' : str
  return (
    <>
      <span title={str}>{display}</span>
      {refLabel && <div className="text-muted">{refLabel}</div>}
    </>
  )
}

export function AdminTablePageView({ tableName, data, filters, onFilter }: AdminTablePageViewProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [pendingDelete, setPendingDelete] = useState<TableRow | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [localFilters, setLocalFilters] = useState<Record<string, string>>(filters)

  const deleteMutation = useMutation({
    mutationFn: (row: TableRow) => {
      const pkFields: Record<string, string> = {}
      for (const [k, v] of Object.entries(row.pk_values)) {
        pkFields[k] = String(v)
      }
      return adminTableDelete(tableName, pkFields)
    },
    onSuccess: (msg) => {
      setFlash(msg)
      setError(null)
      dialogRef.current?.close()
      void queryClient.invalidateQueries({ queryKey: ['admin-table', tableName] })
    },
    onError: (err: Error) => {
      setError(err.message)
      dialogRef.current?.close()
    },
  })

  const totalPages = Math.ceil(data.total / data.limit)
  const currentPage = data.page

  function buildPageLink(page: number) {
    const params = new URLSearchParams({ ...filters, page: String(page) })
    return `?${params.toString()}`
  }

  function handleSubmitFilter(e: React.FormEvent) {
    e.preventDefault()
    onFilter({ ...localFilters, page: '1' })
  }

  return (
    <div className="admin-table-page">
      {flash && <div className="admin-flash">{flash}</div>}
      {error && <div className="admin-error">{error}</div>}

      <article className="admin-table-panel">
        <div className="admin-table-panel__head">
          <div>
            <nav className="admin-breadcrumb">
              <Link to={routes.admin}>Админка</Link>
              <span className="admin-breadcrumb-sep">/</span>
              <span>{data.table_title}</span>
            </nav>
            <h2>{data.table_title}</h2>
            <p className="admin-table-subtitle">
              Таблица: <code>{data.table_name}</code> · Записей: {data.total}
            </p>
          </div>
        </div>

        <form className="admin-search-form" onSubmit={handleSubmitFilter}>
          {data.columns.map((col) => (
            <input
              key={col}
              type="text"
              className="admin-search-input"
              placeholder={col}
              value={localFilters[col] ?? ''}
              onChange={(e) =>
                setLocalFilters((prev) => ({ ...prev, [col]: e.target.value }))
              }
            />
          ))}
          <button type="submit" className="button primary">Найти</button>
          {Object.keys(filters).some((k) => k !== 'page' && filters[k]) && (
            <button type="button" className="button ghost" onClick={() => onFilter({})}>Сбросить</button>
          )}
        </form>

        <div className="table-wrapper">
          <table className="admin-data-table">
            <thead>
              <tr>
                {data.columns.map((col) => <th key={col}>{col}</th>)}
                <th className="admin-actions-col">Действия</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.length > 0 ? (
                data.rows.map((entry, idx) => (
                  <tr key={idx}>
                    {data.columns.map((col) => (
                      <td key={col}>
                        {renderCellValue(
                          col,
                          entry.row[col],
                          data.reference_labels?.[col]?.[String(entry.row[col])],
                        )}
                      </td>
                    ))}
                    <td className="admin-actions-col">
                      <span className="admin-row-actions">
                        <Link
                          className="button button-small"
                          to={routes.adminTableEdit(tableName, entry.pk_query)}
                        >
                          ✏️
                        </Link>
                        <button
                          className="button button-small button-danger"
                          onClick={() => {
                            setPendingDelete(entry)
                            dialogRef.current?.showModal()
                          }}
                        >
                          🗑️
                        </button>
                      </span>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={data.columns.length + 1} className="text-center text-muted">
                    Нет записей
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {data.total > data.limit && (
          <div className="admin-pagination">
            {currentPage > 1 && (
              <button
                className="button ghost"
                onClick={() => onFilter({ ...filters, page: String(currentPage - 1) })}
              >
                ← Назад
              </button>
            )}
            <span className="pagination-info">Страница {currentPage} из {totalPages}</span>
            {currentPage < totalPages && (
              <button
                className="button ghost"
                onClick={() => onFilter({ ...filters, page: String(currentPage + 1) })}
              >
                Вперёд →
              </button>
            )}
          </div>
        )}

        <div className="admin-back-link">
          <Link className="button ghost" to={routes.admin}>← К списку таблиц</Link>
        </div>
      </article>

      <dialog className="confirm-dialog" ref={dialogRef}>
        <h3>Подтверждение удаления</h3>
        <p>Вы уверены, что хотите удалить эту запись? Это действие нельзя отменить.</p>
        <div className="confirm-dialog-actions">
          <button
            className="button ghost"
            onClick={() => dialogRef.current?.close()}
          >
            Отмена
          </button>
          <button
            className="button danger"
            disabled={deleteMutation.isPending}
            onClick={() => pendingDelete && deleteMutation.mutate(pendingDelete)}
          >
            Удалить
          </button>
        </div>
      </dialog>
    </div>
  )
}
