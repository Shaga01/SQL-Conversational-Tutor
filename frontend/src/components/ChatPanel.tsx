import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMeta } from '../api'

export type ChatEntry = { role: 'user' | 'assistant'; content: string; meta?: ChatMeta; error?: string }

type Props = {
  entries: ChatEntry[]
  busy: boolean
  exerciseActive: boolean
  hintLevel: number
  onSend: (text: string) => void
  onHint: () => void
  onUseSql: (sql: string) => void
  onStop: () => void
}

const INTENT_LABEL: Record<string, string> = {
  generate_sql: 'Generated SQL',
  explain_sql: 'Query review',
  concept: 'Concept',
  hint: 'Hint',
  other: 'Chat',
}

const SUGGESTIONS = [
  'Which 5 customers spent the most on delivered orders?',
  'What is the difference between WHERE and HAVING?',
  'How many products in each category have never been ordered?',
]

export function ChatPanel({ entries, busy, exerciseActive, hintLevel, onSend, onHint, onUseSql, onStop }: Props) {
  const [text, setText] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [entries])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim() || busy) return
    onSend(text.trim())
    setText('')
  }

  return (
    <section className="panel chat" aria-label="Tutor chat">
      <header className="panelHead">
        <h2>Tutor</h2>
        {exerciseActive && (
          <button className="ghost" onClick={onHint} disabled={busy || hintLevel >= 4}
                  title="Hints get more specific each time: nudge, concept, partial query, solution">
            {hintLevel >= 4 ? 'Solution shown' : hintLevel === 3 ? 'Show solution' : `Hint ${hintLevel + 1}/3`}
          </button>
        )}
      </header>

      <div className="chatLog" aria-live="polite">
        {entries.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.meta && <span className="badge">{INTENT_LABEL[m.meta.intent] ?? m.meta.intent}</span>}
            {m.meta?.note && <p className="note">{m.meta.note}</p>}
            {m.role === 'assistant' ? (
              <div className="md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content || (busy && i === entries.length - 1 ? '…' : '')}</ReactMarkdown>
              </div>
            ) : (
              <p>{m.content}</p>
            )}
            {m.error && <p className="errorText">{m.error}</p>}
            {m.meta?.sql && m.meta.intent === 'generate_sql' && (
              <button className="small" onClick={() => onUseSql(m.meta!.sql)}>Open in editor</button>
            )}
          </div>
        ))}
        {entries.length <= 1 && (
          <div className="suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s} className="chip" onClick={() => onSend(s)} disabled={busy}>{s}</button>
            ))}
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form className="chatInput" onSubmit={submit}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) submit(e)
          }}
          placeholder="Ask about the data, a concept, or paste SQL to review…"
          rows={2}
          aria-label="Message the tutor"
        />
        {busy ? (
          <button type="button" onClick={onStop}>Stop</button>
        ) : (
          <button type="submit" className="primary" disabled={!text.trim()}>Send</button>
        )}
      </form>
    </section>
  )
}
