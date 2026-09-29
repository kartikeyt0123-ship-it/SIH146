/** Application shell: case selection, navigation and the four primary screens. */
import { useEffect, useState } from 'react'
import {
  Database,
  Inbox,
  Network,
  Plus,
  ShieldCheck,
  Upload,
  WifiOff,
} from 'lucide-react'
import { api } from './api'
import { InboxScreen } from './screens/InboxScreen'
import { OverviewScreen } from './screens/OverviewScreen'
import { UploadScreen } from './screens/UploadScreen'
import { WorkspaceScreen } from './screens/WorkspaceScreen'
import { LoginScreen } from './screens/LoginScreen'
import { Callout, ErrorState, Loading } from './components/common'

type Screen = 'upload' | 'overview' | 'inbox' | 'workspace'

export default function App() {
  const [cases, setCases] = useState<any[] | null>(null)
  const [caseId, setCaseId] = useState<string | null>(null)
  const [screen, setScreen] = useState<Screen>('upload')
  const [runId, setRunId] = useState<string | null>(null)
  const [subject, setSubject] = useState<string | null>(null)
  const [health, setHealth] = useState<any>(null)
  const [error, setError] = useState<unknown>(null)
  const [creating, setCreating] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  //: null while we are still asking the service whether a login is needed.
  const [session, setSession] = useState<{ required: boolean; ok: boolean } | null>(null)

  /**
   * Recover when the selected case no longer exists.
   *
   * A tab can outlive the case it is pointed at - deleted in another window, or removed
   * from the database directly. Without this, every action keeps posting to an
   * identifier that is gone and fails with the same error.
   */
  const handleCaseMissing = async () => {
    setNotice(
      'The selected case no longer exists — it was deleted somewhere else. ' +
      'The case list has been refreshed; create or choose a case to continue.',
    )
    setCaseId(null)
    setRunId(null)
    setSubject(null)
    try {
      const body = await api.listCases()
      setCases(body)
      if (body.length) {
        setCaseId(body[0].id)
        setRunId(body[0].latest_run_id ?? null)
        setScreen(body[0].dataset_count ? 'overview' : 'upload')
      }
    } catch {
      /* the list refresh is best effort; the notice already explains the situation */
    }
  }

  const loadCases = async () => {
    try {
      const body = await api.listCases()
      setCases(body)
      setError(null)
      if (!caseId && body.length) {
        setCaseId(body[0].id)
        if (body[0].latest_run_id) setRunId(body[0].latest_run_id)
        setScreen(body[0].dataset_count ? 'overview' : 'upload')
      }
    } catch (err) {
      setError(err)
      setCases([])
    }
  }

  // Ask whether this instance needs a login before loading anything else: on a
  // protected instance every other call would just come back 401.
  useEffect(() => {
    api.authStatus()
      .then((status) => setSession({ required: status.auth_required, ok: status.authenticated }))
      .catch(() => setSession({ required: false, ok: true }))
  }, [])

  useEffect(() => {
    if (!session?.ok) return
    void loadCases()
    api.health().then(setHealth).catch(() => setHealth(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.ok])

  const createCase = async () => {
    const title = window.prompt('Case title', 'Bitcoin investigation')
    if (!title?.trim()) return
    setCreating(true)
    try {
      const created = await api.createCase(title.trim())
      await loadCases()
      setCaseId(created.id)
      setRunId(null)
      setScreen('upload')
    } catch (err) {
      setError(err)
    } finally {
      setCreating(false)
    }
  }

  const startRun = async (datasetId: string) => {
    if (!caseId) return
    try {
      const started = await api.startRun(caseId, datasetId)
      setRunId(started.run_id)
      setScreen('overview')
    } catch (err) {
      setError(err)
    }
  }

  const current = cases?.find((c) => c.id === caseId)

  const navButton = (target: Screen, label: string, Icon: any, disabled = false) => (
    <button
      onClick={() => setScreen(target)}
      aria-current={screen === target ? 'page' : undefined}
      disabled={disabled}
      title={disabled ? 'Available once an analysis run has completed' : undefined}
    >
      <Icon size={13} aria-hidden="true" />
      {label}
    </button>
  )

  if (session === null) {
    return <div className="app"><Loading label="Connecting to the local service…" /></div>
  }
  if (session.required && !session.ok) {
    return (
      <div className="app">
        <LoginScreen onSignedIn={() => setSession({ required: true, ok: true })} />
      </div>
    )
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          <ShieldCheck size={17} aria-hidden="true" />
          ChainLens
          <small>Bitcoin investigation platform</small>
        </span>

        <nav className="nav" aria-label="Primary">
          {navButton('upload', 'Import', Upload)}
          {navButton('overview', 'Overview', Database, !caseId)}
          {navButton('inbox', 'Alerts', Inbox, !runId)}
          {navButton('workspace', 'Workspace', Network, !runId)}
        </nav>

        <div className="topbar-right">
          <label className="sr-only" htmlFor="case-select">Active case</label>
          <select
            id="case-select"
            value={caseId ?? ''}
            onChange={(e) => {
              const next = cases?.find((c) => c.id === e.target.value)
              setCaseId(e.target.value)
              setRunId(next?.latest_run_id ?? null)
              setSubject(null)
              setScreen(next?.dataset_count ? 'overview' : 'upload')
            }}
            style={{ width: 200, background: 'var(--navy-700)', color: 'var(--text-on-navy)',
              borderColor: 'var(--navy-600)' }}
          >
            {(cases ?? []).length === 0 ? <option value="">No cases</option> : null}
            {(cases ?? []).map((item) => (
              <option key={item.id} value={item.id}>{item.title}</option>
            ))}
          </select>
          <button className="btn btn-sm" onClick={createCase} disabled={creating}>
            <Plus size={12} /> New case
          </button>
          {session.required ? (
            <button className="btn btn-sm" title="Sign out of this instance"
              onClick={async () => { await api.logout(); setSession({ required: true, ok: false }) }}>
              Sign out
            </button>
          ) : null}
          <span className="offline-pill" title="This application makes no outbound network requests.">
            <WifiOff size={11} aria-hidden="true" />
            Offline
          </span>
        </div>
      </header>

      <main className="main">
        {notice ? (
          <div style={{ padding: 'var(--s4) var(--s4) 0', maxWidth: 1320, margin: '0 auto' }}>
            <div className="callout callout-warn" role="status">
              {notice}{' '}
              <button className="btn btn-sm" style={{ marginLeft: 8 }}
                onClick={() => setNotice(null)}>
                Dismiss
              </button>
            </div>
          </div>
        ) : null}
        {cases === null ? (
          <Loading label="Connecting to the local service…" />
        ) : error && !cases.length ? (
          <div className="page"><ErrorState error={error} onRetry={loadCases} /></div>
        ) : cases.length === 0 ? (
          <div className="page">
            <div className="page-head">
              <h1>No cases yet</h1>
              <p className="sub">
                A case holds one investigation: its imported data, its analysis runs, the
                alerts they produced and your notes on them.
              </p>
            </div>
            <div className="panel">
              <div className="panel-body">
                <button className="btn btn-primary" onClick={createCase} disabled={creating}>
                  <Plus size={14} /> Create the first case
                </button>
                {health?.model?.status === 'unavailable' ? (
                  <div style={{ marginTop: 14 }}>
                    <Callout tone="warn">
                      <strong>No trained model is loaded.</strong> Rules will still run, and
                      alerts will be marked as unscored rather than treated as normal.
                      Train one with{' '}
                      <code>python -m chainlens.ml.train --input data/samples/ps3_transactions</code>.
                    </Callout>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : screen === 'upload' ? (
          <UploadScreen
            caseId={caseId}
            caseTitle={current?.title ?? ''}
            onImported={() => void loadCases()}
            onStartRun={startRun}
            onCaseMissing={handleCaseMissing}
          />
        ) : screen === 'overview' ? (
          <OverviewScreen
            caseId={caseId}
            activeRunId={runId}
            onStartRun={startRun}
            onOpenInbox={(id) => { setRunId(id); setScreen('inbox') }}
          />
        ) : screen === 'inbox' ? (
          <div className="page-wide" style={{ height: '100%' }}>
            <InboxScreen
              runId={runId}
              caseId={caseId}
              onOpenWorkspace={(value) => { setSubject(value); setScreen('workspace') }}
            />
          </div>
        ) : (
          <div className="page-wide" style={{ height: '100%' }}>
            <WorkspaceScreen runId={runId} subject={subject} onSubjectChange={setSubject} />
          </div>
        )}
      </main>
    </div>
  )
}
