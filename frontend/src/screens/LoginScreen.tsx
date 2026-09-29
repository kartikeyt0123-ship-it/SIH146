/** Sign-in for a deployed instance. A local instance never shows this. */
import { useState } from 'react'
import { LogIn, ShieldCheck } from 'lucide-react'
import { ApiError, api } from '../api'
import { Callout } from '../components/common'

export function LoginScreen({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!password) return
    setBusy(true)
    setError(null)
    try {
      await api.login(password)
      onSignedIn()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not reach the service. Try again.',
      )
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100%', display: 'flex', alignItems: 'center',
        justifyContent: 'center', padding: 'var(--s5)',
      }}
    >
      <div className="panel" style={{ width: '100%', maxWidth: 400 }}>
        <div className="panel-body">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <ShieldCheck size={20} aria-hidden="true" />
            <h2 style={{ fontSize: 'var(--fs-lg)' }}>ChainLens</h2>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
            This instance is password protected. Sign in to continue.
          </p>

          <form onSubmit={submit}>
            <label className="field">
              <span>Password</span>
              {/* A username field is deliberately absent: this build has one shared
                  operator login, not user accounts. */}
              <input
                type="password"
                value={password}
                autoFocus
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
                aria-describedby={error ? 'login-error' : undefined}
              />
            </label>

            {error ? (
              <div id="login-error" style={{ marginBottom: 'var(--s3)' }} role="alert">
                <Callout tone="danger">{error}</Callout>
              </div>
            ) : null}

            <button className="btn btn-primary" type="submit"
              disabled={busy || !password} style={{ width: '100%' }}>
              {busy ? <span className="spinner" aria-hidden="true" /> : <LogIn size={14} />}
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-faint)',
            marginTop: 'var(--s4)', marginBottom: 0 }}>
            One shared operator login — this build has no user accounts or roles.
            Anyone with the password can read and export every case on this instance.
          </p>
        </div>
      </div>
    </div>
  )
}
