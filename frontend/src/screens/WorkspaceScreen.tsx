/** Screen 4: the investigation workspace — graph centre, evidence right, records below. */
import { useCallback, useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { api, btc, compact, formatTime, shortId } from '../api'
import { GraphView } from '../components/GraphView'
import { EvidenceCard } from '../components/EvidenceCard'
import { SourceRowDialog } from '../components/SourceRowDialog'
import { Callout, Empty, ErrorState, Loading } from '../components/common'

export function WorkspaceScreen({
  runId,
  subject,
  onSubjectChange,
}: {
  runId: string | null
  subject: string | null
  onSubjectChange: (subject: string) => void
}) {
  const [payload, setPayload] = useState<any>(null)
  const [detail, setDetail] = useState<any>(null)
  const [alerts, setAlerts] = useState<any[]>([])
  const [openAlert, setOpenAlert] = useState<any>(null)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [hops, setHops] = useState(1)
  const [budget, setBudget] = useState(300)
  const [showObservations, setShowObservations] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [tab, setTab] = useState<'evidence' | 'records' | 'network'>('evidence')
  const [search, setSearch] = useState(subject ?? '')
  const [sourceRowId, setSourceRowId] = useState<number | null>(null)

  const load = useCallback(async () => {
    if (!runId || !subject) return
    setLoading(true)
    setError(null)
    try {
      const [graph, address] = await Promise.all([
        api.graph(runId, { subject, hops, node_budget: budget,
          include_observations: showObservations }),
        api.addressDetail(runId, subject).catch(() => null),
      ])
      setPayload(graph)
      setDetail(address)
      setAlerts(address?.alerts ?? [])
      setSelectedNode(`addr:${subject}`)
      if (address?.alerts?.length) {
        setOpenAlert(await api.getAlert(address.alerts[0].id))
      } else {
        setOpenAlert(null)
      }
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [runId, subject, hops, budget, showObservations])

  useEffect(() => { void load() }, [load])
  useEffect(() => { setSearch(subject ?? '') }, [subject])

  const onSelect = async (id: string, kind: string, raw: any) => {
    setSelectedNode(id)
    if (kind === 'address' && raw?.address && raw.address !== subject) {
      onSubjectChange(raw.address)
    } else if (kind === 'address' && raw?.alert) {
      const match = alerts.find((a) => a.subject_id === raw.address)
      if (match) setOpenAlert(await api.getAlert(match.id))
    }
  }

  if (!runId) {
    return <div className="page"><Empty title="No completed analysis run">
      Run an analysis before opening the workspace.
    </Empty></div>
  }

  return (
    <div className="workspace">
      <div className="toolbar">
        <form
          className="grow"
          style={{ position: 'relative' }}
          onSubmit={(e) => { e.preventDefault(); if (search.trim()) onSubjectChange(search.trim()) }}
        >
          <Search size={13} aria-hidden="true"
            style={{ position: 'absolute', left: 8, top: 9, color: 'var(--text-faint)' }} />
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search an address to centre the view"
            aria-label="Search an address" style={{ paddingLeft: 26 }} />
        </form>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          Hops
          <select value={hops} onChange={(e) => setHops(Number(e.target.value))}
            aria-label="Neighbourhood hops">
            <option value={0}>0</option><option value={1}>1</option>
            <option value={2}>2</option><option value={3}>3</option>
          </select>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          Node budget
          <select value={budget} onChange={(e) => setBudget(Number(e.target.value))}
            aria-label="Maximum nodes to render">
            <option value={100}>100</option><option value={300}>300</option>
            <option value={600}>600</option><option value={1200}>1200</option>
          </select>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          <input type="checkbox" checked={showObservations} style={{ width: 'auto' }}
            onChange={(e) => setShowObservations(e.target.checked)} />
          Network observations
        </label>
      </div>

      {!subject ? (
        <Empty title="Choose a subject" icon="inbox">
          Search an address above, or open an alert from the inbox. The full graph is
          never rendered by default — views are bounded neighbourhoods you expand
          deliberately.
        </Empty>
      ) : error ? (
        <ErrorState error={error} onRetry={load} />
      ) : (
        <div className="workspace-body">
          <div className="graph-pane">
            <GraphView payload={payload} selected={selectedNode} onSelect={onSelect}
              loading={loading} />
          </div>

          <aside className="evidence-pane">
            <div className="tabs" role="tablist">
              <button role="tab" aria-selected={tab === 'evidence'} onClick={() => setTab('evidence')}>
                Evidence {alerts.length ? `(${alerts.length})` : ''}
              </button>
              <button role="tab" aria-selected={tab === 'records'} onClick={() => setTab('records')}>
                Records
              </button>
              <button role="tab" aria-selected={tab === 'network'} onClick={() => setTab('network')}>
                Network
              </button>
            </div>

            {loading && !detail ? <Loading /> : null}

            {tab === 'evidence' ? (
              alerts.length === 0 ? (
                <Empty title="No alert for this address" icon="none">
                  No detector produced a supported pattern here. That is not a statement
                  that the address is legitimate — only that these records show no
                  pattern the product can support.
                </Empty>
              ) : (
                <>
                  {alerts.length > 1 ? (
                    <div style={{ padding: 'var(--s3) var(--s4) 0' }}>
                      <label className="field">
                        <span>Findings for this address</span>
                        <select
                          value={openAlert?.id ?? ''}
                          onChange={async (e) => setOpenAlert(await api.getAlert(e.target.value))}
                        >
                          {alerts.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.pattern_label} — {item.priority_band} {item.priority.toFixed(1)}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  ) : null}
                  {openAlert ? (
                    <EvidenceCard alert={openAlert} onOpenSource={setSourceRowId}
                      onSelectSubject={onSubjectChange} />
                  ) : <Loading />}
                </>
              )
            ) : null}

            {tab === 'records' && detail ? (
              <div style={{ padding: 'var(--s4)' }}>
                <h4 style={{ marginBottom: 8 }}>Observed activity</h4>
                <dl className="kv" style={{ marginBottom: 10 }}>
                  <dt>Transactions</dt>
                  <dd>{compact(detail.observed_activity.transaction_count)}</dd>
                  <dt>Received</dt>
                  <dd>{btc(detail.observed_activity.received_sats)} BTC</dd>
                  <dt>Contributed as input</dt>
                  <dd>{btc(detail.observed_activity.spent_sats)} BTC</dd>
                  <dt>First seen</dt>
                  <dd>{formatTime(detail.observed_activity.first_seen)}</dd>
                  <dt>Last seen</dt>
                  <dd>{formatTime(detail.observed_activity.last_seen)}</dd>
                </dl>
                <Callout>{detail.balance_note}</Callout>

                <h4 style={{ margin: '14px 0 8px' }}>Associated transactions</h4>
                <div className="table-wrap" style={{ maxHeight: 340, overflowY: 'auto' }}>
                  <table className="data">
                    <thead>
                      <tr><th>TXID</th><th>Dir</th><th className="num">Amount</th><th>Observed</th><th /></tr>
                    </thead>
                    <tbody>
                      {detail.transactions.map((tx: any) => (
                        <tr key={tx.txid}>
                          <td className="mono" title={tx.txid}>{shortId(tx.txid, 10, 4)}</td>
                          <td>{tx.direction}</td>
                          <td className="num">
                            {btc(tx.amount_sats)}
                            {tx.amount_discrepancy ? (
                              <span className="badge badge-warn" style={{ marginLeft: 4 }}>±</span>
                            ) : null}
                          </td>
                          <td style={{ fontSize: 11 }}>{tx.observed_at?.slice(0, 16)}</td>
                          <td>
                            <button className="btn btn-sm"
                              onClick={() => setSourceRowId(tx.source_row_id)}>Row</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {detail.transactions_truncated ? (
                  <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    Showing the first 500 transactions.
                  </p>
                ) : null}
              </div>
            ) : null}

            {tab === 'network' && detail ? (
              <div style={{ padding: 'var(--s4)' }}>
                <h4 style={{ marginBottom: 8 }}>Network observations</h4>
                <Callout tone="warn">{detail.network_observations.note}</Callout>
                <div className="table-wrap" style={{ marginTop: 10 }}>
                  <table className="data">
                    <thead><tr><th>Endpoint</th><th className="num">Records</th></tr></thead>
                    <tbody>
                      {detail.network_observations.endpoints.map(([ip, count]: [string, number]) => (
                        <tr key={ip}>
                          <td className="mono">{ip}</td>
                          <td className="num">{compact(count)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {detail.network_observations.endpoints.length === 0 ? (
                  <Empty title="No network observations" icon="none" />
                ) : null}
              </div>
            ) : null}
          </aside>
        </div>
      )}

      {sourceRowId !== null ? (
        <SourceRowDialog sourceRowId={sourceRowId} onClose={() => setSourceRowId(null)} />
      ) : null}
    </div>
  )
}
