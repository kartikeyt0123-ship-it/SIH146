/** Screen 1: create a case, upload a file, choose a validation policy, review the result. */
import { useCallback, useRef, useState } from 'react'
import { CheckCircle2, FileUp, Play, ShieldCheck, X } from 'lucide-react'
import { ApiError, api, compact } from '../api'
import { Callout, ErrorState, Loading } from '../components/common'

export function UploadScreen({
  caseId,
  caseTitle,
  onImported,
  onStartRun,
  onCaseMissing,
}: {
  caseId: string | null
  caseTitle: string
  onImported: (summary: any) => void
  onStartRun: (datasetId: string) => void
  onCaseMissing?: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [mode, setMode] = useState('compatibility')
  const [tolerance, setTolerance] = useState(1)
  const [summary, setSummary] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files?.[0]
    if (dropped) setFile(dropped)
  }, [])

  const upload = async () => {
    if (!file || !caseId) return
    setBusy(true)
    setError(null)
    setSummary(null)
    try {
      const result = await api.uploadDataset(caseId, file, mode, tolerance)
      setSummary(result)
      onImported(result)
    } catch (err) {
      // The case can disappear from under an open tab - deleted in another window, or
      // removed from the database directly. Recover instead of failing repeatedly
      // against an identifier that no longer exists.
      if (err instanceof ApiError && err.code === 'CASE_NOT_FOUND') {
        onCaseMissing?.()
      }
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  if (!caseId) {
    return (
      <div className="page">
        <Callout tone="warn">Create or select a case before importing a dataset.</Callout>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Import and validate</h1>
        <p className="sub">
          Bringing a dataset into <strong>{caseTitle}</strong>. The original file is kept
          byte-for-byte in a restricted archive; analysis reads only sanitised records.
          Any evaluation label column is removed before a record exists.
        </p>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="panel-head">
            <h3>1. Choose a file</h3>
            <span className="hint">CSV now; JSON and XML are documented adapters</span>
          </div>
          <div className="panel-body">
            <div
              className="dropzone"
              data-active={dragging}
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click() }}
              role="button"
              tabIndex={0}
              aria-label="Choose a transaction file to import"
            >
              <FileUp size={22} aria-hidden="true" style={{ marginBottom: 8 }} />
              <strong>Drop a transaction file, or click to browse</strong>
              <span>
                Files without an extension are identified by their content. UTF-8, BOMs,
                quoted cells and Windows line endings are handled.
              </span>
            </div>
            <input
              ref={inputRef}
              type="file"
              className="sr-only"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            {file ? (
              <div style={{ marginTop: 12 }}>
                <span className="file-chip">
                  <CheckCircle2 size={13} style={{ color: 'var(--ok)' }} aria-hidden="true" />
                  {file.name} · {compact(file.size)} bytes
                  <button className="btn btn-sm" onClick={() => setFile(null)} aria-label="Remove file">
                    <X size={11} />
                  </button>
                </span>
              </div>
            ) : null}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h3>2. Validation policy</h3>
          </div>
          <div className="panel-body">
            <div style={{ display: 'grid', gap: 8, marginBottom: 12 }}>
              <label className="radio-card" data-selected={mode === 'compatibility'}>
                <input type="radio" name="mode" checked={mode === 'compatibility'}
                  onChange={() => setMode('compatibility')} />
                <span>
                  <strong>Synthetic compatibility</strong>
                  <span>
                    Imports every parseable record and marks each problem visibly.
                    Synthetic addresses are kept as opaque identifiers. Use this for the
                    supplied sample.
                  </span>
                </span>
              </label>
              <label className="radio-card" data-selected={mode === 'strict'}>
                <input type="radio" name="mode" checked={mode === 'strict'}
                  onChange={() => setMode('strict')} />
                <span>
                  <strong>Strict</strong>
                  <span>
                    Quarantines malformed arrays, invalid numbers and conservation
                    failures above the tolerance. Nothing is repaired in either mode.
                  </span>
                </span>
              </label>
            </div>

            <label className="field">
              <span>Fee conservation tolerance (satoshis)</span>
              <input type="number" min={0} value={tolerance}
                onChange={(e) => setTolerance(Math.max(0, Number(e.target.value) || 0))} />
              <span className="field-hint">
                Inputs − outputs − fee may differ by this much before the row is treated as
                above tolerance. The exact residual is always recorded, even within
                tolerance, and is never used as a detection signal.
              </span>
            </label>

            <button className="btn btn-primary" disabled={!file || busy} onClick={upload}>
              {busy ? <span className="spinner" aria-hidden="true" /> : <ShieldCheck size={14} />}
              {busy ? 'Validating…' : 'Import and validate'}
            </button>
          </div>
        </div>
      </div>

      {busy ? <div className="panel"><Loading label="Parsing, validating and storing rows…" /></div> : null}
      {error ? <div className="panel"><ErrorState error={error} onRetry={upload} /></div> : null}

      {summary ? (
        <div className="panel">
          <div className="panel-head">
            <h3>Import result</h3>
            <span className="hint">
              {summary.reused_existing
                ? 'Identical file already imported into this case — reused, not duplicated'
                : `Detected as ${summary.detected_format.toUpperCase()}`}
            </span>
          </div>
          <div className="panel-body">
            <div className="grid grid-4" style={{ marginBottom: 16 }}>
              <div className="panel stat">
                <div className="label">Rows in file</div>
                <div className="value">{compact(summary.total_rows)}</div>
              </div>
              <div className="panel stat">
                <div className="label">Accepted</div>
                <div className="value" style={{ color: 'var(--ok)' }}>
                  {compact(summary.accepted_clean)}
                </div>
                <div className="note">no warnings</div>
              </div>
              <div className="panel stat">
                <div className="label">With warnings</div>
                <div className="value" style={{ color: 'var(--warn)' }}>
                  {compact(summary.accepted_warning)}
                </div>
                <div className="note">usable, issues recorded</div>
              </div>
              <div className="panel stat">
                <div className="label">Quarantined</div>
                <div className="value" style={{ color: summary.quarantined ? 'var(--danger)' : undefined }}>
                  {compact(summary.quarantined)}
                </div>
                <div className="note">not analysable</div>
              </div>
            </div>

            {summary.reconciles ? (
              <Callout>
                <strong>Counts reconcile.</strong> {compact(summary.accepted_clean)} +{' '}
                {compact(summary.accepted_warning)} + {compact(summary.quarantined)} ={' '}
                {compact(summary.total_rows)} input rows. Every row is accounted for.
              </Callout>
            ) : (
              <Callout tone="danger">
                <strong>Counts do not reconcile.</strong> This is a defect; do not rely on
                this import.
              </Callout>
            )}

            {summary.label_fields_removed?.length ? (
              <Callout tone="warn">
                <strong>Evaluation label removed.</strong> The column(s){' '}
                <code>{summary.label_fields_removed.join(', ')}</code> were removed at the
                ingestion boundary. They are retained only in the restricted source
                archive and never reach a detector, the feature pipeline or the model.
              </Callout>
            ) : null}

            {Object.keys(summary.issue_counts ?? {}).length ? (
              <div style={{ marginTop: 16 }}>
                <h4 style={{ marginBottom: 8 }}>Data-quality findings</h4>
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr><th>Code</th><th className="num">Count</th><th>What it means</th></tr>
                    </thead>
                    <tbody>
                      {Object.entries(summary.issue_counts).map(([code, count]) => (
                        <tr key={code}>
                          <td className="mono">{code}</td>
                          <td className="num">{compact(count as number)}</td>
                          <td style={{ color: 'var(--text-muted)' }}>
                            {summary.issue_text?.[code] ?? ''}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8 }}>
                  These are data-quality findings, not behavioural detections. A fee
                  residual never contributes to a suspicious-pattern score.
                </p>
              </div>
            ) : null}

            <div style={{ marginTop: 16, display: 'flex', gap: 8, alignItems: 'center' }}>
              <button className="btn btn-primary" onClick={() => onStartRun(summary.dataset_id)}>
                <Play size={14} /> Run analysis
              </button>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                SHA-256 {summary.sha256?.slice(0, 16)}… · {summary.validation_mode} mode
              </span>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
