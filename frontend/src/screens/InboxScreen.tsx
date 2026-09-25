/** Screen 3: the alert inbox — sort by priority, filter, review, annotate, export. */
import { useCallback, useEffect, useState } from 'react'
import { Download, Search } from 'lucide-react'
import { api, formatTime, shortId } from '../api'
import { EvidenceCard } from '../components/EvidenceCard'
import {
  Callout,
  Empty,
  ErrorState,
  Loading,
  PriorityBadge,
} from '../components/common'
import { SourceRowDialog } from '../components/SourceRowDialog'

const REVIEW_STATES = ['new', 'under_review', 'relevant', 'false_positive', 'inconclusive']
const PAGE_SIZE = 50

export function InboxScreen({
  runId,
  caseId,
  onOpenWorkspace,
}: {
  runId: string | null
  caseId: string | null
  onOpenWorkspace: (subject: string) => void
}) {
  const [listing, setListing] = useState<any>(null)
  const [alert, setAlert] = useState<any>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [offset, setOffset] = useState(0)
  const [sourceRowId, setSourceRowId] = useState<number | null>(null)
  const [reviewReason, setReviewReason] = useState('')
  const [note, setNote] = useState('')
  const [exported, setExported] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!runId) return
    setLoading(true)
    try {
      const body = await api.listAlerts(runId, { ...filters, limit: PAGE_SIZE, offset })
      setListing(body)
      setError(null)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [runId, filters, offset])

  useEffect(() => { void load() }, [load])

  const openAlert = async (id: string) => {
    try {
      setAlert(await api.getAlert(id))
      setReviewReason('')
      setNote('')
      setExported(null)
    } catch (err) {
      setError(err)
    }
  }

  const setReview = async (state: string) => {
    if (!alert) return
    setBusy(true)
    try {
      await api.setReview(alert.id, state, reviewReason, alert.review_state)
      await openAlert(alert.id)
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const saveNote = async () => {
    if (!alert || !note.trim()) return
    setBusy(true)
    try {
      await api.addNote(alert.id, note.trim())
      setNote('')
      await openAlert(alert.id)
    } finally {
      setBusy(false)
    }
  }

  const exportAlert = async () => {
    if (!alert || !caseId || !runId) return
    setBusy(true)
    try {
      setExported(await api.createExport(caseId, runId, [alert.id]))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  if (!runId) {
    return <div className="page"><Empty title="No completed analysis run">
      Import a dataset and run an analysis to populate the inbox.
    </Empty></div>
  }
  if (error && !listing) return <div className="page"><ErrorState error={error} onRetry={load} /></div>

  const facets = listing?.facets ?? {}
  const alerts = listing?.alerts ?? []

  const filterSelect = (key: string, label: string) => (
    <label key={key}>
      <span className="sr-only">{label}</span>
      <select
        value={filters[key] ?? ''}
        onChange={(e) => {
          setOffset(0)
          setFilters((f) => ({ ...f, [key]: e.target.value }))
        }}
        aria-label={label}
      >
        <option value="">{label}: all</option>
        {Object.entries(facets[key] ?? {}).map(([value, count]) => (
          <option key={value} value={value}>
            {value.replace(/_/g, ' ')} ({String(count)})
          </option>
        ))}
      </select>
    </label>
  )

  return (
    <div className="inbox">
      <div className="inbox-list">
        <div className="toolbar">
          <div className="grow" style={{ position: 'relative' }}>
            <Search size={13} aria-hidden="true"
              style={{ position: 'absolute', left: 8, top: 9, color: 'var(--text-faint)' }} />
            <input type="search" placeholder="Filter by subject address or TXID"
              style={{ paddingLeft: 26 }}
              aria-label="Filter by subject"
              onChange={(e) => {
                const value = e.target.value
                setOffset(0)
                window.clearTimeout((window as any).__inboxTimer)
                ;(window as any).__inboxTimer = window.setTimeout(
                  () => setFilters((f) => ({ ...f, subject: value })), 300)
              }} />
          </div>
          {filterSelect('pattern', 'Pattern')}
          {filterSelect('priority_band', 'Priority')}
          {filterSelect('evidence_strength', 'Evidence')}
          {filterSelect('review_state', 'Review')}
        </div>

        {loading && !alerts.length ? <Loading label="Loading alerts…" /> : null}

        {!loading && alerts.length === 0 ? (
          <Empty title="No alerts match these filters" icon="none">
            {Object.values(filters).some(Boolean)
              ? 'Clear a filter to widen the search.'
              : 'This run produced no alerts. That is a result, not a statement that the data is clean.'}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <caption className="sr-only">Alerts ordered by review priority, highest first</caption>
              <thead>
                <tr>
                  <th>Priority</th><th>Pattern</th><th>Subject</th>
                  <th>Evidence</th><th>Review</th><th>Period</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((item: any) => (
                  <tr key={item.id} className="selectable"
                    aria-selected={alert?.id === item.id}
                    tabIndex={0}
                    onClick={() => openAlert(item.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter') openAlert(item.id) }}>
                    <td><PriorityBadge band={item.priority_band} priority={item.priority} /></td>
                    <td>{item.pattern_label}</td>
                    <td className="mono" title={item.subject_id}>{shortId(item.subject_id, 12, 5)}</td>
                    <td>{item.evidence_strength}</td>
                    <td>{item.review_state.replace(/_/g, ' ')}</td>
                    <td style={{ whiteSpace: 'nowrap', fontSize: 11, color: 'var(--text-muted)' }}>
                      {item.period_start?.slice(0, 10) ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {listing ? (
          <div className="pager">
            <span>
              {listing.total === 0 ? 'No alerts' :
                `${offset + 1}–${Math.min(offset + PAGE_SIZE, listing.total)} of ${listing.total}`}
            </span>
            <span style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-sm" disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
              <button className="btn btn-sm" disabled={offset + PAGE_SIZE >= listing.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
            </span>
          </div>
        ) : null}
      </div>

      <div className="inbox-detail">
        {!alert ? (
          <Empty title="Select an alert" icon="inbox">
            Choose an alert to see what was observed, why it was flagged, which records
            support it and what remains uncertain.
          </Empty>
        ) : (
          <>
            <EvidenceCard
              alert={alert}
              onOpenSource={setSourceRowId}
              onSelectSubject={onOpenWorkspace}
            />

            <div className="evidence-card">
              <h4 style={{ marginBottom: 8 }}>Analyst decision</h4>
              <Callout>
                Recording a decision does not change any model score and does not retrain
                anything. Judgement is kept separate from the algorithmic result.
              </Callout>
              <label className="field" style={{ marginTop: 12 }}>
                <span>Reason (recorded with the change)</span>
                <input type="text" value={reviewReason}
                  onChange={(e) => setReviewReason(e.target.value)}
                  placeholder="e.g. merchant confirmed by external context" />
              </label>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {REVIEW_STATES.filter((s) => s !== alert.review_state).map((state) => (
                  <button key={state} className="btn btn-sm" disabled={busy}
                    onClick={() => setReview(state)}>
                    {state.replace(/_/g, ' ')}
                  </button>
                ))}
              </div>

              {alert.review_history?.length ? (
                <div style={{ marginTop: 12 }}>
                  <h4 style={{ fontSize: 12, marginBottom: 6 }}>Review history</h4>
                  <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11.5, color: 'var(--text-muted)' }}>
                    {alert.review_history.map((event: any, i: number) => (
                      <li key={i}>
                        {formatTime(event.at)} — {event.actor} changed {event.from_state} →{' '}
                        {event.to_state}{event.reason ? `: ${event.reason}` : ''}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <label className="field" style={{ marginTop: 12 }}>
                <span>Note</span>
                <textarea value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="Observations, context obtained elsewhere, next steps" />
              </label>
              <button className="btn" disabled={busy || !note.trim()} onClick={saveNote}>
                Add note
              </button>

              {alert.notes?.length ? (
                <ul style={{ marginTop: 12, paddingLeft: 16, fontSize: 12 }}>
                  {alert.notes.map((item: any) => (
                    <li key={item.id}>
                      <strong>{item.author}</strong> ({formatTime(item.created_at)}): {item.body}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>

            <div className="evidence-card">
              <h4 style={{ marginBottom: 8 }}>Evidence package</h4>
              <button className="btn btn-primary" disabled={busy} onClick={exportAlert}>
                <Download size={13} /> Export this finding
              </button>
              {exported ? (
                <div style={{ marginTop: 10 }}>
                  <p style={{ fontSize: 12, marginBottom: 6 }}>
                    Package {exported.export_id} written with {exported.files.length} files.
                  </p>
                  <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12 }}>
                    {exported.files.map((name: string) => (
                      <li key={name}>
                        <a href={api.exportFileUrl(exported.export_id, name)} download>{name}</a>
                      </li>
                    ))}
                  </ul>
                  <Callout>
                    The manifest records the source hash, detector and model versions and
                    the configuration, so a recipient can detect a change. It is not a
                    legal certification and does not prove the underlying data authentic.
                  </Callout>
                </div>
              ) : null}
            </div>
          </>
        )}
      </div>

      {sourceRowId !== null ? (
        <SourceRowDialog sourceRowId={sourceRowId} onClose={() => setSourceRowId(null)} />
      ) : null}
    </div>
  )
}
