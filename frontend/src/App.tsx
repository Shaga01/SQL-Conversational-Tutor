import { useMemo, useState } from 'react'
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
  const [prompt, setPrompt] = useState('')
  const [sql, setSql] = useState('SELECT * FROM customers LIMIT 10;')
  const [schema, setSchema] = useState('')
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

  useMemo(() => {
    fetch(`${API_BASE}/api/schema`)
      .then((r) => r.json())
      .then((d) => setSchema(d.schema ?? ''))
      .catch(() => setSchema('Could not load schema. Make sure backend is running.'))
  }, [])

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
        body: JSON.stringify({ message: prompt, level }),
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
        body: JSON.stringify({ sql }),
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

  return (
    <main className="page">
      <header>
        <h1>Conversational SQL Tutor</h1>
        <p>Free local-first capstone prototype with SQLCoder + SQLite sandbox.</p>
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
