/** Small shared presentation pieces. */
import type { ReactNode } from 'react'
import {
  AlertTriangle,
  CircleSlash,
  FileQuestion,
  Inbox,
  ServerCrash,
} from 'lucide-react'
import { ApiError } from '../api'

/** Priority and strength are always shown as a word, never as colour alone. */
export function Badge({
  kind,
  children,
}: {
  kind: 'high' | 'medium' | 'low' | 'ok' | 'warn' | 'danger' | 'neutral'
  children: ReactNode
}) {
  return <span className={`badge badge-${kind}`}>{children}</span>
}

export function PriorityBadge({ band, priority }: { band: string; priority?: number }) {
  const kind = band === 'high' ? 'high' : band === 'medium' ? 'medium' : 'low'
  return (
    <Badge kind={kind}>
      {band.toUpperCase()}
      {priority !== undefined ? ` ${priority.toFixed(1)}` : ''}
    </Badge>
  )
}

export function StrengthBadge({ strength }: { strength: string }) {
  const kind = strength === 'high' ? 'ok' : strength === 'medium' ? 'warn' : 'neutral'
  return <Badge kind={kind}>Evidence: {strength}</Badge>
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{label}</p>
    </div>
  )
}

export function Empty({
  title,
  children,
  icon = 'inbox',
}: {
  title: string
  children?: ReactNode
  icon?: 'inbox' | 'file' | 'none'
}) {
  const Icon = icon === 'file' ? FileQuestion : icon === 'none' ? CircleSlash : Inbox
  return (
    <div className="state">
      <Icon className="icon" size={28} aria-hidden="true" />
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
    </div>
  )
}

/** Failure is always shown as failure. No previous or fabricated result is substituted. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const apiError = error instanceof ApiError ? error : null
  const message = apiError ? apiError.message : String(error)
  return (
    <div className="state" role="alert">
      <ServerCrash className="icon state-error" size={28} aria-hidden="true" />
      <h3 className="state-error">Something went wrong</h3>
      <p>{message}</p>
      {apiError?.code ? (
        <p className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          {apiError.code}
        </p>
      ) : null}
      {onRetry ? (
        <button className="btn" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}

export function Callout({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warn' | 'danger'
  children: ReactNode
}) {
  const cls = tone === 'warn' ? 'callout callout-warn' : tone === 'danger' ? 'callout callout-danger' : 'callout'
  return (
    <div className={cls}>
      {tone !== 'info' ? (
        <AlertTriangle size={13} style={{ verticalAlign: '-2px', marginRight: 6 }} aria-hidden="true" />
      ) : null}
      {children}
    </div>
  )
}

export function Stat({
  label,
  value,
  note,
  tone,
}: {
  label: string
  value: ReactNode
  note?: ReactNode
  tone?: 'high' | 'medium' | 'low' | 'ok'
}) {
  const colour =
    tone === 'high' ? 'var(--high)' : tone === 'medium' ? 'var(--medium)'
      : tone === 'ok' ? 'var(--ok)' : undefined
  return (
    <div className="panel stat">
      <div className="label">{label}</div>
      <div className="value" style={colour ? { color: colour } : undefined}>{value}</div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  )
}

export function Disclosure({
  summary,
  children,
  count,
  open,
}: {
  summary: string
  children: ReactNode
  count?: number
  open?: boolean
}) {
  return (
    <details className="disclosure" open={open}>
      <summary>
        <span>
          {summary}
          {count !== undefined ? (
            <span style={{ color: 'var(--text-faint)', fontWeight: 400 }}> ({count})</span>
          ) : null}
        </span>
      </summary>
      <div>{children}</div>
    </details>
  )
}

export function BarRow({
  name,
  value,
  max,
  colour = 'var(--navy-600)',
}: {
  name: string
  value: number
  max: number
  colour?: string
}) {
  const pct = max > 0 ? Math.max(2, (value / max) * 100) : 0
  return (
    <div className="bar-row">
      <span className="name">{name}</span>
      <span className="track">
        <span className="fill" style={{ width: `${pct}%`, background: colour }} />
      </span>
      <span className="n">{value.toLocaleString('en-US')}</span>
    </div>
  )
}
