import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'

type Level = 'beginner' | 'intermediate' | 'advanced'

type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
}

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

function App() {
  const [level, setLevel] = useState<Level>('beginner')
  const [sessionId, setSessionId] = useState('default')
  const [prompt, setPrompt] = useState('')
  const [sql, setSql] = useState('SELECT * FROM customers LIMIT 10;')
  const [schema, setSchema] = useState('')
  const [schemaSql, setSchemaSql] = useState(
    'CREATE TABLE IF NOT EXISTS employees (id INTEGER PRIMARY KEY, name TEXT, team TEXT);',
  )
  const [seedSql, setSeedSql] = useState(
    "INSERT INTO employees (name, team) VALUES ('Ana', 'Data'), ('Lee', 'Platform');",
  )
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: 'assistant',
      content:
        'Welcome! Ask for SQL in English, or paste SQL and click "Explain SQL" to get step-by-step tutoring feedback.',
    },
  ])
  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadSchema = () => {
    fetch(`${API_BASE}/api/schema?session_id=${encodeURIComponent(sessionId)}`)
      .then((r) => r.json())
      .then((d) => setSchema(d.schema ?? ''))
      .catch(() => setSchema('Could not load schema. Make sure backend is running.'))
  }

  useEffect(() => {
    loadSchema()
  }, [sessionId])

  const addMsg = (role: 'user' | 'assistant', content: string) => {
    setMessages((prev) => [...prev, { role, content }])
  }

  const handleTutor = async (e: FormEvent) => {
    e.preventDefault()
    if (!prompt.trim()) return
    setError('')
    setLoading(true)
    addMsg('user', prompt)

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: prompt, level, session_id: sessionId }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Request failed')
      addMsg('assistant', data.response ?? '')
      if (data.sql) setSql(data.sql)
      setPrompt('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const explainSql = async () => {
    if (!sql.trim()) return
    setError('')
    setLoading(true)
    addMsg('user', `Explain this SQL:\n${sql}`)
    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: 'Please explain my SQL',
          level,
          sql,
          session_id: sessionId,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Explain failed')
      addMsg('assistant', data.response ?? '')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const runSql = async () => {
    setError('')
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql, session_id: sessionId }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Execution failed')
      setColumns(data.columns ?? [])
      setRows(data.rows ?? [])
      addMsg('assistant', `Query executed successfully. Returned ${data.row_count} row(s).`)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const applySchema = async () => {
    setError('')
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/schema/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          schema_sql: schemaSql,
          seed_sql: seedSql,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Schema apply failed')
      addMsg('assistant', data.message ?? 'Schema applied.')
      loadSchema()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="page">
      <header>
        <h1>Conversational SQL Tutor</h1>
        <p>Free local-first capstone prototype with SQLCoder + SQLite sandbox.</p>
        <label>
          Session ID:
          <input
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
            placeholder="default or project_xyz"
          />
        </label>
      </header>

      <section className="layout">
        <div className="panel">
          <h2>Tutor Chat</h2>
          <label>
            Level:
            <select value={level} onChange={(e) => setLevel(e.target.value as Level)}>
              <option value="beginner">Beginner</option>
              <option value="intermediate">Intermediate</option>
              <option value="advanced">Advanced</option>
            </select>
          </label>
          <div className="chatBox">
            {messages.map((m, idx) => (
              <div key={idx} className={`msg ${m.role}`}>
                <strong>{m.role === 'user' ? 'You' : 'Tutor'}:</strong> {m.content}
              </div>
            ))}
          </div>
          <form onSubmit={handleTutor} className="prompt">
            <input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g., Write SQL to show total orders per customer"
            />
            <button type="submit" disabled={loading}>
              Ask
            </button>
          </form>
        </div>

        <div className="panel">
          <h2>SQL Workspace</h2>
          <textarea value={sql} onChange={(e) => setSql(e.target.value)} rows={12} />
          <div className="actions">
            <button onClick={runSql} disabled={loading}>
              Run SQL
            </button>
            <button onClick={explainSql} disabled={loading}>
              Explain SQL
            </button>
          </div>
          {error ? <p className="error">{error}</p> : null}
        </div>

        <div className="panel">
          <h2>Schema</h2>
          <textarea
            value={schemaSql}
            onChange={(e) => setSchemaSql(e.target.value)}
            rows={5}
            placeholder="Paste CREATE TABLE statements here"
          />
          <textarea
            value={seedSql}
            onChange={(e) => setSeedSql(e.target.value)}
            rows={4}
            placeholder="Optional INSERT statements"
          />
          <div className="actions">
            <button onClick={applySchema} disabled={loading}>
              Apply Custom Schema
            </button>
            <button onClick={loadSchema} disabled={loading}>
              Refresh Schema
            </button>
          </div>
          <pre>{schema || 'Loading schema...'}</pre>
        </div>
      </section>

      <section className="panel resultPanel">
        <h2>Results</h2>
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => (
                    <td key={c}>{String(row[c] ?? '')}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length ? <p>No rows yet. Run a query to see output.</p> : null}
        </div>
      </section>
    </main>
  )
}

export default App
