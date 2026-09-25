/**
 * The original uploaded row, reachable from any alert in two clicks.
 *
 * This is the one place the raw record is shown, label column included, clearly marked
 * as the restricted archive rather than analysis input.
 */
import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '../api'
import { Callout, ErrorState, Loading } from './common'

export function SourceRowDialog({
  sourceRowId,
  onClose,
}: {
  sourceRowId: number
  onClose: () => void
}) {
  const [row, setRow] = useState<any>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    api.sourceRow(sourceRowId)
      .then((body) => { if (!cancelled) setRow(body) })
      .catch((err) => { if (!cancelled) setError(err) })
    return () => { cancelled = true }
  }, [sourceRowId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Original source row"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(15,37,64,0.45)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50,
        padding: 24,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="panel" style={{ maxWidth: 780, width: '100%', maxHeight: '86vh',
        overflow: 'auto', boxShadow: 'var(--shadow-lg)' }}>
        <div className="panel-head">
          <h3>Original source row {row ? `#${row.row_number}` : ''}</h3>
          <button className="btn btn-sm" onClick={onClose} aria-label="Close"><X size={12} /></button>
        </div>
        <div className="panel-body">
          {error ? <ErrorState error={error} /> : !row ? <Loading /> : (
            <>
              <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                From {row.dataset.original_name} · SHA-256 {row.dataset.sha256?.slice(0, 20)}…
                · status {row.status}
              </p>
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Field</th><th>Value as supplied</th></tr></thead>
                  <tbody>
                    {Object.entries(row.raw).map(([key, value]) => {
                      const isLabel = /suspicious|pattern_type|label/i.test(key)
                      return (
                        <tr key={key} style={isLabel ? { background: 'var(--warn-bg)' } : undefined}>
                          <td className="mono">
                            {key}
                            {isLabel ? (
                              <span className="badge badge-warn" style={{ marginLeft: 6 }}>
                                evaluation label — removed before analysis
                              </span>
                            ) : null}
                          </td>
                          <td className="mono" style={{ wordBreak: 'break-all' }}>{String(value)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {row.issues?.length ? (
                <div style={{ marginTop: 12 }}>
                  <h4 style={{ fontSize: 12, marginBottom: 6 }}>Validation findings on this row</h4>
                  <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}>
                    {row.issues.map((issue: any, i: number) => (
                      <li key={i}>
                        <strong className="mono">{issue.code}</strong> ({issue.severity}):{' '}
                        {issue.message}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div style={{ marginTop: 12 }}>
                <Callout>{row.note}</Callout>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
