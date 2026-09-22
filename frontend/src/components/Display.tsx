import { useState } from 'react'
import type { Finding, QueryResult, Stage, TraceStep } from '../api'

const cell = (v: unknown) => (v === null ? <span className="null">NULL</span> : String(v))

export function ResultTable({ result, max = 200 }: { result: Pick<QueryResult, 'columns' | 'rows'> & Partial<QueryResult>; max?: number }) {
  if (!result.columns.length) return <p className="muted">The query returned no columns.</p>
  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>{result.columns.map((c, i) => <th key={i}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {result.rows.slice(0, max).map((row, i) => (
            <tr key={i}>{row.map((v, j) => <td key={j}>{cell(v)}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {!result.rows.length && <p className="muted pad">No rows.</p>}
      {result.truncated && <p className="muted pad">Showing the first {result.rows.length} rows.</p>}
    </div>
  )
}

/** Logical execution order, one card per clause, with the number of rows that survive it. */
export function StepFlow({ stages }: { stages: Stage[] }) {
  const [open, setOpen] = useState<number | null>(null)
  if (!stages.length) return <p className="muted">Run "Visualize" to see how the database executes your query step by step.</p>
  const max = Math.max(1, ...stages.map((s) => s.row_count ?? 0))
  return (
    <ol className="stepFlow">
      {stages.map((s, i) => (
        <li key={i} className={s.error ? 'stepErr' : ''}>
          <button className="stepHead" onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}>
            <span className="stepIdx">{i + 1}</span>
            <span className="stepClause">{s.clause}</span>
            <span className="stepBar">
              <span style={{ width: `${Math.max(2, (100 * (s.row_count ?? 0)) / max)}%` }} />
            </span>
            <span className="stepRows">{s.row_count ?? '–'} rows</span>
          </button>
          <p className="stepText">{s.error ? `Could not show this step: ${s.error}` : s.explanation}</p>
          {open === i && !s.error && (
            <div className="stepDetail">
              <pre className="code">{s.sql}</pre>
              <ResultTable result={{ columns: s.columns, rows: s.preview }} />
            </div>
          )}
        </li>
      ))}
    </ol>
  )
}

const ICON: Record<Finding['severity'], string> = { error: '✖', warning: '▲', info: 'ℹ' }

export function FindingsList({ findings, empty }: { findings: Finding[]; empty?: string }) {
  if (!findings.length) return <p className="muted">{empty ?? 'No problems detected.'}</p>
  return (
    <ul className="findings">
      {findings.map((f, i) => (
        <li key={i} className={`finding ${f.severity}`}>
          <span className="fIcon" aria-label={f.severity}>{ICON[f.severity]}</span>
          <div>
            <strong>{f.title}</strong>
            <p>{f.message}</p>
            {f.evidence && <code>{f.evidence}</code>}
          </div>
        </li>
      ))}
    </ul>
  )
}

const STAGE_LABEL: Record<string, string> = {
  schema_linking: 'Schema linking',
  value_hints: 'Value retrieval',
  few_shot: 'Example retrieval',
  generate: 'Generate',
  self_correct: 'Self-correct',
  vote: 'Vote',
}

/** The text-to-SQL agent's reasoning trace. */
export function TraceView({ trace }: { trace: TraceStep[] }) {
  if (!trace.length) return <p className="muted">Ask the tutor a question about the data to see the agent pipeline at work.</p>
  return (
    <ol className="trace">
      {trace.map((t, i) => (
        <li key={i}>
          <span className="traceStage">{STAGE_LABEL[t.stage] ?? t.stage}</span>
          <span className="traceDetail">{t.detail}</span>
          <span className="traceMs">{t.ms >= 1000 ? `${(t.ms / 1000).toFixed(1)}s` : `${Math.round(t.ms)}ms`}</span>
        </li>
      ))}
    </ol>
  )
}
