/** Typed client for the local API. Same-origin only; no external services. */

export interface ApiErrorBody {
  code: string
  message: string
  [key: string]: unknown
}

export class ApiError extends Error {
  code: string
  status: number
  body: ApiErrorBody

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code || 'UNKNOWN'
    this.body = body
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (cause) {
    // A failed fetch here means the local backend is not reachable, not that the
    // machine is offline: the application never talks to anything else.
    throw new ApiError(0, {
      code: 'BACKEND_UNREACHABLE',
      message:
        'The local ChainLens service did not respond. Confirm it is running with ' +
        '"python -m chainlens".',
      cause: String(cause),
    })
  }

  if (!response.ok) {
    let body: ApiErrorBody = { code: 'HTTP_ERROR', message: response.statusText }
    try {
      const parsed = await response.json()
      body = (parsed?.detail ?? parsed) as ApiErrorBody
    } catch {
      /* keep the status-text fallback */
    }
    throw new ApiError(response.status, body)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => request<any>('/api/health'),
  policy: () => request<any>('/api/meta/policy'),

  listCases: () => request<any[]>('/api/cases'),
  createCase: (title: string, description = '') =>
    request<any>('/api/cases', json({ title, description })),
  getCase: (caseId: string) => request<any>(`/api/cases/${caseId}`),
  deleteCase: (caseId: string) => request<any>(`/api/cases/${caseId}`, { method: 'DELETE' }),

  uploadDataset: (caseId: string, file: File, mode: string, feeTolerance: number) => {
    const form = new FormData()
    form.append('file', file)
    form.append('validation_mode', mode)
    form.append('fee_tolerance_sats', String(feeTolerance))
    return request<any>(`/api/cases/${caseId}/imports`, { method: 'POST', body: form })
  },
  datasetIssues: (datasetId: string, code?: string, limit = 100, offset = 0) =>
    request<any>(`/api/datasets/${datasetId}/issues${qs({ code, limit, offset })}`),
  datasetRows: (datasetId: string, status?: string, limit = 50, offset = 0) =>
    request<any>(`/api/datasets/${datasetId}/rows${qs({ status, limit, offset })}`),
  sourceRow: (id: number) => request<any>(`/api/source-rows/${id}`),

  startRun: (caseId: string, datasetId: string, detectorConfig?: unknown) =>
    request<any>(`/api/cases/${caseId}/runs`,
      json({ dataset_id: datasetId, detector_config: detectorConfig ?? null })),
  getRun: (runId: string) => request<any>(`/api/runs/${runId}`),
  cancelRun: (runId: string) => request<any>(`/api/runs/${runId}/cancel`, { method: 'POST' }),

  listAlerts: (runId: string, params: Record<string, any> = {}) =>
    request<any>(`/api/runs/${runId}/alerts${qs(params)}`),
  getAlert: (alertId: string) => request<any>(`/api/alerts/${alertId}`),
  setReview: (alertId: string, state: string, reason: string, expected?: string) =>
    request<any>(`/api/alerts/${alertId}/review`, {
      ...json({ state, reason, expected_state: expected ?? null }),
      method: 'PATCH',
    }),
  addNote: (alertId: string, body: string) =>
    request<any>(`/api/alerts/${alertId}/notes`, json({ body })),
  sensitivity: (alertId: string, options: Record<string, unknown>) =>
    request<any>(`/api/alerts/${alertId}/sensitivity`, json(options)),

  graph: (runId: string, params: Record<string, any>) =>
    request<any>(`/api/runs/${runId}/graph${qs(params)}`),
  addressDetail: (runId: string, address: string) =>
    request<any>(`/api/runs/${runId}/addresses/${encodeURIComponent(address)}`),
  clusters: (runId: string) => request<any[]>(`/api/runs/${runId}/clusters`),

  createExport: (caseId: string, runId: string, alertIds?: string[]) =>
    request<any>(`/api/cases/${caseId}/exports`,
      json({ run_id: runId, alert_ids: alertIds ?? null })),
  exportFileUrl: (exportId: string, filename: string) =>
    `/api/exports/${exportId}/files/${encodeURIComponent(filename)}`,

  audit: (caseId: string) => request<any>(`/api/cases/${caseId}/audit`),
}

/** Satoshis to a BTC string. Integer maths only; no floating-point rounding. */
export function btc(sats: number | null | undefined, decimals = 8): string {
  if (sats === null || sats === undefined || Number.isNaN(sats)) return '—'
  const negative = sats < 0
  const value = Math.abs(Math.round(sats))
  const whole = Math.floor(value / 100_000_000)
  const fraction = String(value % 100_000_000).padStart(8, '0')
  const text = `${whole}.${decimals >= 8 ? fraction : fraction.slice(0, decimals)}`
  return `${negative ? '-' : ''}${text}`
}

export const compact = (n: number | null | undefined): string =>
  n === null || n === undefined ? '—' : n.toLocaleString('en-US')

export const shortId = (value: string, head = 10, tail = 6): string =>
  value.length <= head + tail + 1 ? value : `${value.slice(0, head)}…${value.slice(-tail)}`

export const formatTime = (iso: string | null | undefined): string =>
  !iso ? '—' : iso.replace('T', ' ').replace('Z', ' UTC')
