/** Screen 2: dataset coverage, run status, data-quality impact and analysis progress. */
import { useEffect, useState } from 'react'
import { Activity, Ban, Database, Play } from 'lucide-react'
import { api, compact, formatTime } from '../api'
import { BarRow, Callout, Empty, ErrorState, Loading, Stat } from '../components/common'

const STAGE_LABELS: Record<string, string> = {
  queued: 'Queued',
  loading: 'Loading sanitised records',
  graph: 'Building the graph',
  structure_rules: 'Structural detectors',
  features: 'Extracting address features',
  model: 'Scoring with the frozen model',
  behaviour_rules: 'Behavioural detectors and safeguards',
  scoring: 'Combining scores',
  alerts: 'Writing alerts',
  complete: 'Complete',
}

const DETECTOR_LABELS: Record<string, string> = {
  coinjoin_like: 'CoinJoin-like structure',
  collection_pattern: 'Collection pattern',
  payment_participant: 'Payment participant',
  peeling_sequence: 'Peeling sequence',
  common_control: 'Candidate common control',
  shared_ip_observation: 'Shared network observation',
  high_activity_context: 'High activity (context)',
  statistical_anomaly: 'Statistical anomaly',
}

export function OverviewScreen({
  caseId,
  activeRunId,
  onStartRun,
  onOpenInbox,
}: {
  caseId: string | null
  activeRunId: string | null
  onStartRun: (datasetId: string) => void
  onOpenInbox: (runId: string) => void
}) {
  const [detail, setDetail] = useState<any>(null)
  const [error, setError] = useState<unknown>(null)
  const [run, setRun] = useState<any>(null)

  const load = async () => {
    if (!caseId) return
    try {
      setDetail(await api.getCase(caseId))
      setError(null)
    } catch (err) {
      setError(err)
    }
  }

  useEffect(() => { void load() /* eslint-disable-next-line */ }, [caseId])

  // Poll while a run is in flight. Simple polling, no websockets, no broker.
  useEffect(() => {
    if (!activeRunId) return
    let cancelled = false
    const tick = async () => {
      try {
        const body = await api.getRun(activeRunId)
        if (cancelled) return
        setRun(body)
        if (body.status === 'running' || body.status === 'queued') {
          setTimeout(tick, 600)
        } else {
          void load()
        }
      } catch (err) {
        if (!cancelled) setError(err)
      }
    }
    void tick()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRunId])

  if (!caseId) return <div className="page"><Empty title="No case selected">Create a case to begin.</Empty></div>
  if (error) return <div className="page"><ErrorState error={error} onRetry={load} /></div>
  if (!detail) return <div className="page"><Loading /></div>

  const datasets = detail.datasets ?? []
  const latestDataset = datasets[0]
  const runs = detail.runs ?? []
  const activeRun = run ?? runs.find((r: any) => r.id === activeRunId) ?? runs[0]
  const stats = activeRun?.stats ?? {}
  const coverage = stats.coverage ?? {}
  const counts = stats.counts_by_detector ?? {}
  const maxCount = Math.max(1, ...Object.values(counts).map((v) => Number(v)))

  if (!datasets.length) {
    return (
      <div className="page">
        <Empty title="No data imported yet" icon="file">
          This case exists but has no dataset. Import a transaction file to begin. An
          empty case is not the same as a case with no findings.
        </Empty>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>{detail.case.title}</h1>
        <p className="sub">
          Case {detail.case.id} · state {detail.case.state.replace(/_/g, ' ')} · last
          updated {formatTime(detail.case.updated_at)}
        </p>
      </div>

      {/* ---------------------------------------------------------- run status */}
      {activeRun && (activeRun.status === 'running' || activeRun.status === 'queued') ? (
        <div className="panel">
          <div className="panel-head">
            <h3><Activity size={14} style={{ verticalAlign: '-2px' }} /> Analysis in progress</h3>
            <button className="btn btn-sm btn-danger" onClick={() => api.cancelRun(activeRun.id)}>
              <Ban size={12} /> Cancel
            </button>
          </div>
          <div className="panel-body">
            <p style={{ marginBottom: 8 }}>
              {STAGE_LABELS[activeRun.stage] ?? activeRun.stage} ·{' '}
              {Math.round((activeRun.progress ?? 0) * 100)}%
            </p>
            <div className="progress">
              <div style={{ width: `${(activeRun.progress ?? 0) * 100}%` }} />
            </div>
          </div>
        </div>
      ) : null}

      {activeRun?.status === 'failed' ? (
        <div className="panel">
          <div className="panel-body">
            <Callout tone="danger">
              <strong>The analysis failed.</strong> {activeRun.error}
              <br />
              No partial results are shown, and no earlier run is being displayed in its
              place. Fix the cause and start a new run.
            </Callout>
          </div>
        </div>
      ) : null}

      {activeRun?.status === 'cancelled' ? (
        <div className="panel">
          <div className="panel-body">
            <Callout tone="warn">
              <strong>The analysis was cancelled.</strong> Any partial results were
              discarded, so nothing incomplete is presented as a finding.
            </Callout>
          </div>
        </div>
      ) : null}

      {/* ------------------------------------------------------------ coverage */}
      <div className="grid grid-4">
        <Stat label="Transactions" value={compact(coverage.transactions ?? latestDataset.total_rows)}
          note={coverage.period_start ? `${coverage.period_start.slice(0, 10)} → ${coverage.period_end?.slice(0, 10)}` : 'imported rows'} />
        <Stat label="Addresses" value={compact(coverage.addresses ?? 0)}
          note="distinct identifiers observed" />
        <Stat label="Network observations" value={compact(coverage.observations ?? 0)}
          note={`${compact(coverage.distinct_src_ips ?? 0)} distinct endpoints`} />
        <Stat
          label="Alerts"
          value={compact(
            activeRun?.alert_count ??
            Object.values(detail.alert_counts_by_band ?? {})
              .reduce((total: number, count) => total + Number(count), 0),
          )}
          note={activeRun?.status === 'complete' ? 'from the latest run' : 'no completed run'}
        />
      </div>

      {/* --------------------------------------------------- data quality panel */}
      <div className="panel">
        <div className="panel-head">
          <h3><Database size={14} style={{ verticalAlign: '-2px' }} /> Data quality and its impact</h3>
          <span className="hint">{latestDataset.original_name} · {latestDataset.validation_mode} mode</span>
        </div>
        <div className="panel-body">
          <div className="grid grid-3" style={{ marginBottom: 12 }}>
            <div>
              <div className="stat" style={{ padding: 0 }}>
                <div className="label">Accepted without warning</div>
                <div className="value" style={{ fontSize: 20, color: 'var(--ok)' }}>
                  {compact(latestDataset.accepted_clean)}
                </div>
              </div>
            </div>
            <div>
              <div className="stat" style={{ padding: 0 }}>
                <div className="label">Accepted with warnings</div>
                <div className="value" style={{ fontSize: 20, color: 'var(--warn)' }}>
                  {compact(latestDataset.accepted_warning)}
                </div>
              </div>
            </div>
            <div>
              <div className="stat" style={{ padding: 0 }}>
                <div className="label">Quarantined</div>
                <div className="value" style={{ fontSize: 20 }}>
                  {compact(latestDataset.quarantined)}
                </div>
              </div>
            </div>
          </div>

          {latestDataset.reconciles ? (
            <Callout>
              All {compact(latestDataset.total_rows)} input rows reconcile to the three
              categories above. Source SHA-256 {latestDataset.sha256?.slice(0, 20)}…
            </Callout>
          ) : (
            <Callout tone="danger">Import counts do not reconcile.</Callout>
          )}

          {coverage.transactions_with_amount_discrepancy ? (
            <Callout tone="warn">
              <strong>
                {compact(coverage.transactions_with_amount_discrepancy)} transactions have
                amounts that do not reconcile with their supplied fee.
              </strong>{' '}
              Structural conclusions about those records still stand; any amount-derived
              figure drawn from them is marked provisional on the alert. This is a
              data-quality finding and is never used as a reason to suspect an address.
            </Callout>
          ) : null}

          {latestDataset.label_column_removed ? (
            <Callout>
              An evaluation label column was present in the source file and was removed
              before any analysable record was created.
            </Callout>
          ) : null}
        </div>
      </div>

      {/* --------------------------------------------------------- run results */}
      {activeRun?.status === 'complete' ? (
        <div className="panel">
          <div className="panel-head">
            <h3>Findings by detector</h3>
            <span className="hint">
              run {activeRun.id} · {stats.total_seconds}s · model {activeRun.model_status}
            </span>
          </div>
          <div className="panel-body">
            {Object.keys(counts).length === 0 ? (
              <Empty title="No findings" icon="none">
                The run completed and found no supported pattern. That is a result, not a
                certificate of legitimacy.
              </Empty>
            ) : (
              <>
                {Object.entries(counts).map(([key, value]) => (
                  <BarRow key={key} name={DETECTOR_LABELS[key] ?? key}
                    value={Number(value)} max={maxCount}
                    colour={key === 'shared_ip_observation' || key === 'high_activity_context'
                      ? 'var(--node-endpoint)' : 'var(--navy-600)'} />
                ))}
                <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 10 }}>
                  {compact(stats.collaborative_transactions_excluded ?? 0)} collaborative
                  transactions were excluded from ownership merging.{' '}
                  {compact(stats.addresses_scored ?? 0)} addresses scored,{' '}
                  {compact(stats.addresses_unscored ?? 0)} unscored.{' '}
                  {stats.collection_candidates
                    ? `${compact(stats.collection_candidates.below_report_threshold ?? 0)} collection candidates cleared the payer floor but not the evidence bar and were not raised as alerts.`
                    : ''}
                </p>
                <button className="btn btn-primary" style={{ marginTop: 12 }}
                  onClick={() => onOpenInbox(activeRun.id)}>
                  Open alert inbox
                </button>
              </>
            )}
          </div>
        </div>
      ) : null}

      {/* -------------------------------------------------------- dataset list */}
      <div className="panel">
        <div className="panel-head"><h3>Imported datasets</h3></div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>File</th><th>Format</th><th>Mode</th>
                <th className="num">Rows</th><th className="num">Quarantined</th>
                <th>Imported</th><th />
              </tr>
            </thead>
            <tbody>
              {datasets.map((dataset: any) => (
                <tr key={dataset.id}>
                  <td>{dataset.original_name}</td>
                  <td>{dataset.detected_format}</td>
                  <td>{dataset.validation_mode}</td>
                  <td className="num">{compact(dataset.total_rows)}</td>
                  <td className="num">{compact(dataset.quarantined)}</td>
                  <td>{formatTime(dataset.imported_at)}</td>
                  <td>
                    <button className="btn btn-sm" onClick={() => onStartRun(dataset.id)}>
                      <Play size={11} /> Analyse
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {runs.length ? (
        <div className="panel">
          <div className="panel-head"><h3>Analysis runs</h3></div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Run</th><th>Status</th><th>Detectors</th><th>Model</th>
                  <th>Started</th><th>Finished</th><th />
                </tr>
              </thead>
              <tbody>
                {runs.map((item: any) => (
                  <tr key={item.id}>
                    <td className="mono">{item.id}</td>
                    <td>{item.status}</td>
                    <td className="mono" style={{ fontSize: 11 }}>{item.detector_version}</td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {item.model_version ?? '—'} ({item.model_status})
                    </td>
                    <td>{formatTime(item.started_at)}</td>
                    <td>{formatTime(item.finished_at)}</td>
                    <td>
                      {item.status === 'complete' ? (
                        <button className="btn btn-sm" onClick={() => onOpenInbox(item.id)}>
                          Alerts
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  )
}
