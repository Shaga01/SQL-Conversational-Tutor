import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import { api, streamChat } from './api'
import type { Exercise, Finding, Health, LearnerProfile, QueryResult, Skill, Stage, SubmitResponse, Table, TraceStep } from './api'
import { ChatPanel } from './components/ChatPanel'
import type { ChatEntry } from './components/ChatPanel'
import { FindingsList, ResultTable, StepFlow, TraceView } from './components/Display'
import { CustomDbPanel, ExerciseList, ProgressPanel, SchemaPanel } from './components/Sidebar'
import { SqlEditor } from './components/SqlEditor'

type SideTab = 'exercises' | 'progress' | 'schema' | 'custom'
type OutTab = 'results' | 'steps' | 'feedback' | 'agent'
type Level = 'auto' | 'beginner' | 'intermediate' | 'advanced'

function learnerId(): string {
  try {
    let id = localStorage.getItem('sqltutor.learner')
    if (!id) {
      id = crypto.randomUUID()
      localStorage.setItem('sqltutor.learner', id)
    }
    return id
  } catch {
    return 'anonymous'
  }
}

const WELCOME: ChatEntry = {
  role: 'assistant',
  content:
    "Hi! I'm your SQL tutor. Pick an **exercise** on the left, ask me a question about the shop data, " +
    'or paste a query and I will walk through it step by step.',
}

export default function App() {
  const lid = useMemo(learnerId, [])
  const [dark, setDark] = useState(() => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false)
  const [health, setHealth] = useState<Health | null>(null)
  const [dbId, setDbId] = useState('shop')
  const [databases, setDatabases] = useState<{ db_id: string; kind: string }[]>([])
  const [tables, setTables] = useState<Table[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [exercises, setExercises] = useState<Exercise[]>([])
  const [profile, setProfile] = useState<LearnerProfile | null>(null)
  const [level, setLevel] = useState<Level>('auto')

  const [sideTab, setSideTab] = useState<SideTab>('exercises')
  const [outTab, setOutTab] = useState<OutTab>('results')
  const [active, setActive] = useState<Exercise | null>(null)
  const [hintLevel, setHintLevel] = useState(0)
  const [sql, setSql] = useState('SELECT first_name, last_name, city\nFROM customers\nWHERE country = \'USA\'\nLIMIT 10;')

  const [result, setResult] = useState<QueryResult | null>(null)
  const [runError, setRunError] = useState('')
  const [findings, setFindings] = useState<Finding[]>([])
  const [stages, setStages] = useState<Stage[]>([])
  const [trace, setTrace] = useState<TraceStep[]>([])
  const [submission, setSubmission] = useState<SubmitResponse | null>(null)
  const [working, setWorking] = useState(false)

  const [chat, setChat] = useState<ChatEntry[]>([WELCOME])
  const [chatBusy, setChatBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  }, [dark])

  const refreshLearner = useCallback(() => {
    api.learner(lid).then(setProfile).catch(() => undefined)
    api.exercises(lid).then(setExercises).catch(() => undefined)
  }, [lid])

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
    api.skills().then(setSkills).catch(() => undefined)
    api.databases().then(setDatabases).catch(() => undefined)
    refreshLearner()
  }, [refreshLearner])

  useEffect(() => {
    api.schema(dbId).then(setTables).catch(() => setTables([]))
  }, [dbId])

  const clearOutput = () => {
    setResult(null)
    setRunError('')
    setFindings([])
    setStages([])
    setSubmission(null)
  }

  const runSql = useCallback(async (override?: string) => {
    setWorking(true)
    setSubmission(null)
    try {
      const r = await api.run(active?.db_id ?? dbId, override ?? sql)
      setFindings(r.findings)
      if (r.ok) {
        setResult(r)
        setRunError('')
        setOutTab(r.findings.some((f) => f.severity !== 'info') ? 'feedback' : 'results')
      } else {
        setResult(null)
        setRunError(r.error)
        setOutTab('feedback')
      }
    } catch (e) {
      setRunError((e as Error).message)
      setOutTab('feedback')
    } finally {
      setWorking(false)
    }
  }, [active, dbId, sql])

  const visualize = async () => {
    setWorking(true)
    try {
      setStages((await api.trace(active?.db_id ?? dbId, sql)).stages)
      setOutTab('steps')
    } finally {
      setWorking(false)
    }
  }

  const submit = async () => {
    if (!active) return
    setWorking(true)
    try {
      const r = await api.submit(active.id, lid, sql, hintLevel)
      setSubmission(r)
      setFindings(r.findings)
      setRunError('')
      if (r.result) setResult(r.result)
      setOutTab('feedback')
      refreshLearner()
    } catch (e) {
      setRunError((e as Error).message)
      setOutTab('feedback')
    } finally {
      setWorking(false)
    }
  }

  const pickExercise = (e: Exercise | null) => {
    setActive(e)
    setHintLevel(0)
    clearOutput()
    if (e) {
      setDbId(e.db_id)
      setSql(`-- ${e.prompt}\n`)
    }
  }

  const sendChat = async (message: string, opts: { hint?: number } = {}) => {
    const history = chat.filter((m) => m !== WELCOME).map(({ role, content }) => ({ role, content }))
    setChat((c) => [...c, { role: 'user', content: message }, { role: 'assistant', content: '' }])
    setChatBusy(true)
    const controller = new AbortController()
    abortRef.current = controller
    const patchLast = (fn: (m: ChatEntry) => ChatEntry) =>
      setChat((c) => [...c.slice(0, -1), fn(c[c.length - 1])])
    try {
      await streamChat(
        {
          learner_id: lid, db_id: active?.db_id ?? dbId, message, history, level,
          exercise_id: active?.id ?? null, current_sql: sql, hint_level: opts.hint ?? 0,
        },
        {
          meta: (meta) => {
            patchLast((m) => ({ ...m, meta }))
            if (meta.stages.length) setStages(meta.stages)
            if (meta.pipeline_trace.length) setTrace(meta.pipeline_trace)
          },
          token: (t) => patchLast((m) => ({ ...m, content: m.content + t })),
          error: (err) => patchLast((m) => ({ ...m, error: err })),
        },
        controller.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') patchLast((m) => ({ ...m, error: (e as Error).message }))
    } finally {
      setChatBusy(false)
      abortRef.current = null
    }
  }

  const askHint = () => {
    const next = Math.min(hintLevel + 1, 4)
    setHintLevel(next)
    sendChat(next === 4 ? 'Please show me the solution and explain it.' : "I'd like a hint.", { hint: next })
  }

  const explain = () => sendChat(`Explain my query:\n\`\`\`sql\n${sql}\n\`\`\``)

  const createDb = async (name: string, schema: string, seed: string) => {
    const { db_id } = await api.createDatabase(name, schema, seed)
    setDatabases(await api.databases())
    pickExercise(null)
    setDbId(db_id)
    setSql(`SELECT * FROM ${schema.match(/create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)/i)?.[1] ?? 'sqlite_master'} LIMIT 10;`)
    setSideTab('schema')
  }

  const warnings = findings.filter((f) => f.severity !== 'info').length
  const hasQuery = sql.replace(/--[^\n]*|\/\*[\s\S]*?\*\//g, '').trim().length > 0

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>⌗</span>
          <div>
            <h1>SQL Tutor</h1>
            <p className="muted small">Explanation-first SQL learning with a local AI</p>
          </div>
        </div>
        <div className="topControls">
          <label>
            Database
            <select value={dbId} onChange={(e) => { pickExercise(null); setDbId(e.target.value) }}>
              {databases.map((d) => <option key={d.db_id} value={d.db_id}>{d.db_id}</option>)}
            </select>
          </label>
          <label>
            Explanations
            <select value={level} onChange={(e) => setLevel(e.target.value as Level)}>
              <option value="auto">Adaptive ({profile?.level ?? '…'})</option>
              <option value="beginner">Beginner</option>
              <option value="intermediate">Intermediate</option>
              <option value="advanced">Advanced</option>
            </select>
          </label>
          <span className={`status ${health?.llm_available ? 'on' : 'off'}`}
                title={health?.llm_available ? `Models: ${health.sql_model}` : 'Start Ollama for AI features; the analyzer still works offline.'}>
            {health?.llm_available ? 'AI online' : 'AI offline'}
          </span>
          <button className="ghost icon" onClick={() => setDark(!dark)} aria-label="Toggle dark mode">{dark ? '☀' : '☾'}</button>
        </div>
      </header>

      <main className="layout">
        <aside className="panel side">
          <nav className="tabs" role="tablist">
            {(['exercises', 'progress', 'schema', 'custom'] as SideTab[]).map((t) => (
              <button key={t} role="tab" aria-selected={sideTab === t} className={sideTab === t ? 'on' : ''} onClick={() => setSideTab(t)}>
                {{ exercises: 'Exercises', progress: 'Progress', schema: 'Schema', custom: 'My DB' }[t]}
              </button>
            ))}
          </nav>
          <div className="sideBody">
            {sideTab === 'exercises' && (
              <ExerciseList exercises={exercises} skills={skills} activeId={active?.id ?? null}
                            recommendedId={profile?.next?.id ?? null} onPick={pickExercise} />
            )}
            {sideTab === 'progress' && (
              <ProgressPanel profile={profile} skills={skills}
                             onReset={async () => { await api.resetLearner(lid); refreshLearner() }} />
            )}
            {sideTab === 'schema' && <SchemaPanel tables={tables} />}
            {sideTab === 'custom' && <CustomDbPanel onCreate={createDb} />}
          </div>
        </aside>

        <section className="work">
          {active && (
            <div className="panel exercise">
              <div className="exHead">
                <h2>{active.title}</h2>
                <span className="dots" aria-label={`difficulty ${active.difficulty} of 5`}>{'●'.repeat(active.difficulty)}{'○'.repeat(5 - active.difficulty)}</span>
              </div>
              <p>{active.prompt}</p>
              <div className="skillTags">
                {active.skills.map((s) => <span key={s} className="tag">{skills.find((k) => k.id === s)?.name ?? s}</span>)}
              </div>
            </div>
          )}

          <div className="panel editorPanel">
            <SqlEditor value={sql} onChange={setSql} tables={tables} onRun={() => runSql()} dark={dark} />
            <div className="actions">
              <button onClick={() => runSql()} disabled={working} title="Ctrl/Cmd + Enter">▶ Run</button>
              <button onClick={visualize} disabled={working}>Visualize steps</button>
              <button onClick={explain} disabled={chatBusy}>Explain</button>
              {active && <button className="primary" onClick={submit} disabled={working || !hasQuery}>Submit answer</button>}
            </div>
          </div>

          <div className="panel output">
            <nav className="tabs" role="tablist">
              {(['results', 'steps', 'feedback', 'agent'] as OutTab[]).map((t) => (
                <button key={t} role="tab" aria-selected={outTab === t} className={outTab === t ? 'on' : ''} onClick={() => setOutTab(t)}>
                  {{ results: 'Results', steps: 'Execution steps', feedback: 'Feedback', agent: 'Agent trace' }[t]}
                  {t === 'feedback' && (warnings > 0 || runError) ? <span className="pill">{warnings || '!'}</span> : null}
                </button>
              ))}
            </nav>
            <div className="outBody">
              {outTab === 'results' && (result ? (
                <>
                  <p className="muted small">{result.row_count} row(s) · {result.elapsed_ms} ms</p>
                  <ResultTable result={result} />
                </>
              ) : <p className="muted">{runError ? 'The query failed. See Feedback.' : 'Run a query to see results.'}</p>)}
              {outTab === 'steps' && <StepFlow stages={stages} />}
              {outTab === 'feedback' && (
                <div className="feedback">
                  {submission && (
                    <div className={`verdict ${submission.correct ? 'ok' : 'bad'}`}>
                      <strong>{submission.correct ? 'Correct!' : 'Not quite yet.'}</strong>
                      {submission.diff && !submission.correct && <p>{submission.diff.summary}</p>}
                      {Object.entries(submission.mastery_changes).map(([s, c]) => (
                        <span key={s} className="delta">
                          {skills.find((k) => k.id === s)?.name ?? s}: {Math.round(c.before * 100)}% → {Math.round(c.after * 100)}%
                        </span>
                      ))}
                      {submission.correct && submission.next && (
                        <button className="small" onClick={() => pickExercise(submission.next)}>
                          Next: {submission.next.title}
                        </button>
                      )}
                    </div>
                  )}
                  {runError && <p className="errorText">{runError}</p>}
                  <FindingsList findings={findings} empty={submission || result ? 'No problems detected.' : 'Run or submit a query to get feedback.'} />
                  {submission && !submission.correct && submission.diff && (submission.diff.missing.length > 0 || submission.diff.extra.length > 0) && (
                    <div className="diffSamples">
                      {submission.diff.missing.length > 0 && <p className="small"><strong>Some expected rows you are missing:</strong> {submission.diff.missing.map((r) => `(${r.join(', ')})`).join(' ')}</p>}
                      {submission.diff.extra.length > 0 && <p className="small"><strong>Some rows that should not be there:</strong> {submission.diff.extra.map((r) => `(${r.join(', ')})`).join(' ')}</p>}
                    </div>
                  )}
                </div>
              )}
              {outTab === 'agent' && <TraceView trace={trace} />}
            </div>
          </div>
        </section>

        <ChatPanel
          entries={chat}
          busy={chatBusy}
          exerciseActive={!!active}
          hintLevel={hintLevel}
          onSend={(t) => sendChat(t)}
          onHint={askHint}
          onUseSql={(s) => { setSql(s); runSql(s) }}
          onStop={() => abortRef.current?.abort()}
        />
      </main>
    </div>
  )
}
