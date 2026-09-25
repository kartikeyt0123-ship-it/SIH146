/**
 * The evidence card.
 *
 * It answers four questions in a fixed order: what happened, why it was flagged, which
 * records support it, and what remains uncertain. Every number shown here comes from the
 * stored run; nothing is computed in the browser and nothing is placeholder text.
 */
import { useState } from 'react'
import { ExternalLink, FileText, Scale } from 'lucide-react'
import { api, btc, compact, formatTime, shortId } from '../api'
import { Badge, Callout, Disclosure, PriorityBadge, StrengthBadge } from './common'

const ROLE_LABELS: Record<string, string> = {
  unknown_role: 'Unknown role',
  collection_point: 'Collection point',
  potential_payment_participant: 'Potential payment participant',
  sequence_continuation: 'Sequence continuation',
  sequence_side_recipient: 'Sequence side recipient',
  collaborative_transaction_participant: 'Collaborative transaction participant',
  possible_common_control_member: 'Possible common-control member',
  high_activity_service_like: 'High activity, service-like profile',
}

/** Indicator keys rendered as satoshi amounts rather than raw integers. */
const SATS_KEYS = new Set([
  'incoming_sats_in_window', 'subsequent_outflow_sats', 'mean_payment_sats',
  'denomination_sats', 'total_peeled_off_sats', 'entry_value_sats',
  'final_continuation_sats', 'received_sats', 'spent_sats', 'paid_sats',
])

const HIDDEN_KEYS = new Set(['severity_breakdown', 'addresses', 'members', 'txids',
  'continuation_addresses', 'side_recipient_addresses', 'subject_pairs'])

function formatIndicator(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (Array.isArray(value)) return `${value.length} item${value.length === 1 ? '' : 's'}`
  if (typeof value === 'number') {
    if (SATS_KEYS.has(key)) return `${btc(value)} BTC`
    return Number.isInteger(value) ? compact(value) : value.toFixed(4)
  }
  return String(value)
}

const humanise = (key: string) => key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())

export function EvidenceCard({
  alert,
  onOpenSource,
  onSelectSubject,
}: {
  alert: any
  onOpenSource?: (sourceRowId: number) => void
  onSelectSubject?: (subject: string) => void
}) {
  const [sensitivity, setSensitivity] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  const components = alert.priority_components ?? {}
  const indicators = alert.indicators ?? {}
  const modelUnavailable = components.model_component === 'unavailable'
  const suppression = components.suppression_applied

  const runSensitivity = async (options: Record<string, unknown>) => {
    setBusy(true)
    try {
      setSensitivity(await api.sensitivity(alert.id, options))
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className="evidence-card">
      <h3>{alert.pattern_label}</h3>
      <div className="card-meta">
        <PriorityBadge band={alert.priority_band} priority={alert.priority} />
        <StrengthBadge strength={alert.evidence_strength} />
        <Badge kind="neutral">{ROLE_LABELS[alert.role_hypothesis] ?? alert.role_hypothesis}</Badge>
        <Badge kind="neutral">{alert.review_state.replace(/_/g, ' ')}</Badge>
      </div>

      <div className="card-meta">
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {alert.subject_type === 'address' ? 'Address' : 'Transaction'}
        </span>
        <button
          className="subject-id"
          style={{ cursor: onSelectSubject ? 'pointer' : 'default', border: '1px solid var(--line)' }}
          onClick={() => onSelectSubject?.(alert.subject_id)}
          title={alert.subject_id}
        >
          {shortId(alert.subject_id, 20, 8)}
        </button>
      </div>

      {/* 1. What happened */}
      <dl className="qa">
        <dt>What happened</dt>
        <dd>{alert.explanation}</dd>

        {/* 2. Why it was flagged */}
        <dt>Why this was flagged</dt>
        <dd>
          <div className="score-bar">
            <span style={{ width: 96, color: 'var(--text-muted)', fontSize: 11 }}>Rule severity</span>
            <span className="track">
              <span className="fill" style={{ width: `${components.rule_severity ?? 0}%` }} />
            </span>
            <span className="n">{(components.rule_severity ?? 0).toFixed(0)}</span>
          </div>
          <div className="score-bar">
            <span style={{ width: 96, color: 'var(--text-muted)', fontSize: 11 }}>Anomaly pct.</span>
            <span className="track">
              <span
                className="fill"
                style={{
                  width: `${components.anomaly_percentile ?? 0}%`,
                  background: 'var(--node-transaction)',
                }}
              />
            </span>
            <span className="n">
              {components.anomaly_percentile === null || components.anomaly_percentile === undefined
                ? 'n/a'
                : components.anomaly_percentile.toFixed(1)}
            </span>
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', margin: '6px 0 0' }}>
            {components.applied_formula}
          </p>
          {modelUnavailable ? (
            <Callout tone="warn">
              <strong>Unscored by the model.</strong> {components.unscored_reason} Priority
              reflects rule severity only. The address is not treated as normal.
            </Callout>
          ) : null}
          {suppression ? (
            <Callout tone="warn">
              <strong>Held down deliberately.</strong> Uncapped priority would be{' '}
              {suppression.uncapped_priority}; it is capped at {suppression.ceiling}.{' '}
              {suppression.reason}
            </Callout>
          ) : null}
        </dd>

        {Array.isArray(indicators.severity_breakdown) && indicators.severity_breakdown.length ? (
          <>
            <dt>How the severity was reached</dt>
            <dd>
              <ul>
                {indicators.severity_breakdown.map((line: string, i: number) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            </dd>
          </>
        ) : null}

        {/* 3. Which records support it */}
        <dt>Which records support it</dt>
        <dd>
          <p style={{ marginBottom: 6 }}>
            {alert.evidence?.length ?? alert.evidence_count ?? 0} supporting record
            {(alert.evidence?.length ?? alert.evidence_count) === 1 ? '' : 's'}, observed{' '}
            {formatTime(alert.period_start)} to {formatTime(alert.period_end)}.
          </p>
          {alert.evidence?.length ? (
            <div className="table-wrap" style={{ maxHeight: 220, overflowY: 'auto' }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>TXID</th>
                    <th>Row</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {alert.evidence.slice(0, 40).map((item: any, i: number) => (
                    <tr key={i}>
                      <td className="mono" title={item.txid ?? ''}>
                        {item.txid ? shortId(item.txid, 12, 6) : '—'}
                      </td>
                      <td className="num">{item.row_number ?? '—'}</td>
                      <td>
                        {item.source_row_id && onOpenSource ? (
                          <button
                            className="btn btn-sm"
                            onClick={() => onOpenSource(item.source_row_id)}
                            title="Open the original source row"
                          >
                            <FileText size={11} /> Source
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Callout>No individual records were attached to this finding.</Callout>
          )}
          {alert.evidence?.length > 40 ? (
            <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
              Showing the first 40. The export contains all {alert.evidence.length}.
            </p>
          ) : null}
        </dd>

        {/* 4. What remains uncertain */}
        <dt>What remains uncertain</dt>
        <dd>
          {alert.alternatives?.length ? (
            <>
              <p style={{ marginBottom: 4, fontWeight: 600, fontSize: 12 }}>
                Other explanations that fit these records
              </p>
              <ul style={{ marginBottom: 10 }}>
                {alert.alternatives.map((text: string, i: number) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
            </>
          ) : null}
          {alert.caveats?.length ? (
            <>
              <p style={{ marginBottom: 4, fontWeight: 600, fontSize: 12 }}>Limitations</p>
              <ul>
                {alert.caveats.map((text: string, i: number) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
            </>
          ) : null}
        </dd>
      </dl>

      <Disclosure summary="Measured values" count={Object.keys(indicators).length}>
        <dl className="kv">
          {Object.entries(indicators)
            .filter(([key]) => !HIDDEN_KEYS.has(key))
            .map(([key, value]) => (
              <div key={key} style={{ display: 'contents' }}>
                <dt>{humanise(key)}</dt>
                <dd>{formatIndicator(key, value)}</dd>
              </div>
            ))}
        </dl>
      </Disclosure>

      <Disclosure summary="Why this evidence strength" count={alert.evidence_reasons?.length}>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
          {(alert.evidence_reasons ?? []).map((reason: string, i: number) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
      </Disclosure>

      {alert.address_score?.features ? (
        <Disclosure summary="Model features for this address">
          <dl className="kv" style={{ fontSize: 12 }}>
            {Object.entries(alert.address_score.features).map(([key, value]) => (
              <div key={key} style={{ display: 'contents' }}>
                <dt>{humanise(key)}</dt>
                <dd>{typeof value === 'number' ? value.toFixed(3) : String(value)}</dd>
              </div>
            ))}
          </dl>
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
            These are the only inputs the model received. Addresses, TXIDs, IP addresses
            and evaluation labels are excluded by an allowlist.
          </p>
        </Disclosure>
      ) : null}

      {/* Evidence sensitivity: ranking recalculation only */}
      <Disclosure summary="Evidence sensitivity — test this ranking">
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Recalculate this alert's priority with a component removed. This changes the
          ranking only: the model is not rescored and the stored alert does not change.
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
          <button className="btn btn-sm" disabled={busy}
            onClick={() => runSensitivity({ drop_model_component: true })}>
            <Scale size={12} /> Without the model score
          </button>
          <button className="btn btn-sm" disabled={busy}
            onClick={() => runSensitivity({ drop_rule_component: true })}>
            <Scale size={12} /> Without the rule severity
          </button>
          {sensitivity ? (
            <button className="btn btn-sm" onClick={() => setSensitivity(null)}>Reset</button>
          ) : null}
        </div>
        {sensitivity ? (
          <div>
            <dl className="kv" style={{ fontSize: 12 }}>
              <dt>Baseline priority</dt>
              <dd>{sensitivity.baseline.priority.toFixed(2)} ({sensitivity.baseline.band})</dd>
              <dt>Revised priority</dt>
              <dd>{sensitivity.revised.priority.toFixed(2)} ({sensitivity.revised.band})</dd>
              <dt>Change</dt>
              <dd>{sensitivity.delta > 0 ? '+' : ''}{sensitivity.delta.toFixed(2)}</dd>
              <dt>Changes applied</dt>
              <dd>{sensitivity.changes.join(', ')}</dd>
            </dl>
            <Callout>{sensitivity.statement}</Callout>
          </div>
        ) : null}
      </Disclosure>

      <p style={{ fontSize: 10.5, color: 'var(--text-faint)', marginTop: 10, marginBottom: 0 }}>
        Alert {alert.id} · run {alert.run_id} · created {formatTime(alert.created_at)}
        {onSelectSubject ? (
          <>
            {' · '}
            <button
              className="btn btn-sm"
              style={{ padding: '1px 6px' }}
              onClick={() => onSelectSubject(alert.subject_id)}
            >
              <ExternalLink size={10} /> Open in graph
            </button>
          </>
        ) : null}
      </p>
    </article>
  )
}
