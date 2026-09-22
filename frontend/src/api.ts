// Typed client for the FastAPI backend. In dev, Vite proxies /api to :8000.
const BASE = import.meta.env.VITE_API_BASE ?? ''

export type Finding = {
  id: string
  severity: 'error' | 'warning' | 'info'
  skill: string
  title: string
  message: string
  evidence: string
}

export type QueryResult = {
  columns: string[]
  rows: unknown[][]
  row_count: number
  truncated: boolean
  elapsed_ms: number
}

export type RunResponse =
  | ({ ok: true; findings: Finding[] } & QueryResult)
  | { ok: false; error: string; findings: Finding[] }

export type Stage = {
  clause: string
  sql: string
  explanation: string
  row_count: number | null
  columns: string[]
  preview: unknown[][]
  error: string
}

export type TraceStep = { stage: string; detail: string; ms: number; data: Record<string, unknown> }

export type Column = { name: string; type: string; pk: boolean; references: string | null; description: string; samples: string[] }
export type Table = { name: string; description: string; row_count: number; columns: Column[] }

export type Exercise = {
  id: string
  title: string
  prompt: string
  skills: string[]
  difficulty: number
  db_id: string
  solved: boolean
}

export type Skill = { id: string; name: string; summary: string; prereqs: string[] }

export type LearnerProfile = {
  learner_id: string
  level: 'beginner' | 'intermediate' | 'advanced'
  mastery: Record<string, number>
  unlocked: string[]
  solved: string[]
  misconceptions: Record<string, number>
  history: { exercise_id: string; correct: boolean; hints_used: number; at: number }[]
  next: (Exercise & { reason: string }) | null
}

export type ResultDiff = {
  match: boolean
  expected_rows: number
  actual_rows: number
  expected_cols: number
  actual_cols: number
  missing: unknown[][]
  extra: unknown[][]
  order_only: boolean
  summary: string
}

export type SubmitResponse = {
  correct: boolean
  findings: Finding[]
  diff: ResultDiff | null
  result: QueryResult | null
  mastery_changes: Record<string, { before: number; after: number }>
  next: (Exercise & { reason: string }) | null
  solution: string | null
}

export type ChatMeta = {
  intent: string
  sql: string
  findings: Finding[]
  stages: Stage[]
  result: QueryResult | null
  pipeline_trace: TraceStep[]
  note: string
  level: string
}

export type Health = { status: string; llm_available: boolean; sql_model: string; tutor_model: string }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
  return body as T
}

const post = <T>(path: string, data: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(data) })

export const api = {
  health: () => request<Health>('/api/health'),
  databases: () => request<{ db_id: string; kind: string }[]>('/api/databases'),
  createDatabase: (name: string, schema_sql: string, seed_sql: string) =>
    post<{ db_id: string }>('/api/databases', { name, schema_sql, seed_sql }),
  schema: (dbId: string) => request<Table[]>(`/api/databases/${encodeURIComponent(dbId)}/schema`),
  run: (db_id: string, sql: string) => post<RunResponse>('/api/query/run', { db_id, sql }),
  trace: (db_id: string, sql: string) => post<{ stages: Stage[] }>('/api/query/trace', { db_id, sql }),
  skills: () => request<Skill[]>('/api/skills'),
  exercises: (learnerId: string) => request<Exercise[]>(`/api/exercises?learner_id=${encodeURIComponent(learnerId)}`),
  submit: (exerciseId: string, learner_id: string, sql: string, hints_used: number) =>
    post<SubmitResponse>(`/api/exercises/${exerciseId}/submit`, { learner_id, sql, hints_used }),
  learner: (learnerId: string) => request<LearnerProfile>(`/api/learners/${encodeURIComponent(learnerId)}`),
  resetLearner: (learnerId: string) =>
    request<{ status: string }>(`/api/learners/${encodeURIComponent(learnerId)}`, { method: 'DELETE' }),
}

export type ChatRequest = {
  learner_id: string
  db_id: string
  message: string
  history: { role: 'user' | 'assistant'; content: string }[]
  level: 'auto' | 'beginner' | 'intermediate' | 'advanced'
  exercise_id: string | null
  current_sql: string
  hint_level: number
}

/** POST /api/chat and consume the Server-Sent Events stream. */
export async function streamChat(
  req: ChatRequest,
  on: { meta: (m: ChatMeta) => void; token: (t: string) => void; error: (e: string) => void },
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  })
  if (!res.ok || !res.body) {
    on.error(`Chat failed: HTTP ${res.status}`)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      let event = 'message'
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      const payload = data ? JSON.parse(data) : null
      if (event === 'meta') on.meta(payload as ChatMeta)
      else if (event === 'token') on.token(payload as string)
      else if (event === 'error') on.error(payload as string)
    }
  }
}
