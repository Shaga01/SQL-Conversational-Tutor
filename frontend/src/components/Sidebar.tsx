import { useState } from 'react'
import type { Exercise, LearnerProfile, Skill, Table } from '../api'

const MISCONCEPTION_NAMES: Record<string, string> = {
  'null-equality': 'Comparing with = NULL',
  'cartesian-join': 'Join without condition',
  'wrong-join-key': 'Wrong join key',
  'ungrouped-column': 'Column missing from GROUP BY',
  'aggregate-in-where': 'Aggregate in WHERE',
  'left-join-nullified': 'WHERE undoing a LEFT JOIN',
  'count-star-left-join': 'COUNT(*) after LEFT JOIN',
  'limit-without-order': 'LIMIT without ORDER BY',
  'integer-division': 'Integer division',
  'double-quoted-string': 'Double-quoted text',
  'literal-case': 'Wrong capitalisation',
  'unknown-column': 'Unknown column',
  'unknown-table': 'Unknown table',
  'ambiguous-column': 'Ambiguous column',
  'wrong-result': 'Wrong result',
  'wrong-columns': 'Wrong columns',
  'wrong-order': 'Wrong order',
  'needs-outer-join': 'Needed a LEFT JOIN',
  'missing-distinct': 'Missing DISTINCT',
  syntax: 'Syntax error',
  'clause-order': 'Clause order',
  'trailing-comma': 'Trailing comma',
}

export function ExerciseList({ exercises, skills, activeId, recommendedId, onPick }: {
  exercises: Exercise[]
  skills: Skill[]
  activeId: string | null
  recommendedId: string | null
  onPick: (e: Exercise | null) => void
}) {
  const bySkill = skills
    .map((s) => ({ skill: s, items: exercises.filter((e) => e.skills[0] === s.id) }))
    .filter((g) => g.items.length)
  return (
    <div className="exerciseList">
      {activeId && <button className="ghost full" onClick={() => onPick(null)}>Leave exercise (free practice)</button>}
      {bySkill.map(({ skill, items }) => (
        <div key={skill.id} className="exGroup">
          <h3>{skill.name}</h3>
          {items.map((e) => (
            <button key={e.id} className={`exItem ${e.id === activeId ? 'active' : ''}`} onClick={() => onPick(e)}>
              <span className={`check ${e.solved ? 'done' : ''}`} aria-label={e.solved ? 'solved' : 'unsolved'}>{e.solved ? '✓' : ''}</span>
              <span className="exTitle">{e.title}</span>
              {e.id === recommendedId && <span className="badge rec">next</span>}
              <span className="dots" aria-label={`difficulty ${e.difficulty} of 5`}>{'●'.repeat(e.difficulty)}</span>
            </button>
          ))}
        </div>
      ))}
    </div>
  )
}

export function ProgressPanel({ profile, skills, onReset }: { profile: LearnerProfile | null; skills: Skill[]; onReset: () => void }) {
  if (!profile) return <p className="muted">Loading…</p>
  const mis = Object.entries(profile.misconceptions).slice(0, 6)
  return (
    <div className="progress">
      <div className="levelCard">
        <span className="muted">Estimated level</span>
        <strong className="levelName">{profile.level}</strong>
        <span className="muted">{profile.solved.length} exercises solved</span>
      </div>
      {profile.next && (
        <p className="nextHint"><strong>Recommended next:</strong> {profile.next.title}. {profile.next.reason}</p>
      )}
      <h3>Skill mastery</h3>
      <p className="muted small">Bayesian Knowledge Tracing: the probability you have mastered each skill.</p>
      <ul className="mastery">
        {skills.map((s) => {
          const p = profile.mastery[s.id] ?? 0
          const locked = !profile.unlocked.includes(s.id)
          return (
            <li key={s.id} className={locked ? 'locked' : ''} title={locked ? `Unlocks after: ${s.prereqs.join(', ')}` : s.summary}>
              <span className="mName">{locked ? '🔒 ' : ''}{s.name}</span>
              <span className="mBar"><span style={{ width: `${Math.round(p * 100)}%` }} className={p >= 0.9 ? 'mastered' : ''} /></span>
              <span className="mPct">{Math.round(p * 100)}%</span>
            </li>
          )
        })}
      </ul>
      <h3>Your recurring mistakes</h3>
      {mis.length ? (
        <ul className="misList">
          {mis.map(([id, n]) => <li key={id}><span>{MISCONCEPTION_NAMES[id] ?? id}</span><span className="count">×{n}</span></li>)}
        </ul>
      ) : (
        <p className="muted small">None yet. Submit exercises to build your profile.</p>
      )}
      <button className="ghost full" onClick={onReset}>Reset my progress</button>
    </div>
  )
}

export function SchemaPanel({ tables }: { tables: Table[] }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <div className="schema">
      {tables.map((t) => (
        <div key={t.name} className="tbl">
          <button className="tblHead" onClick={() => setOpen(open === t.name ? null : t.name)} aria-expanded={open === t.name}>
            <span>{t.name}</span>
            <span className="muted small">{t.row_count} rows</span>
          </button>
          {open === t.name && (
            <div className="tblBody">
              {t.description && <p className="muted small">{t.description}</p>}
              <ul>
                {t.columns.map((c) => (
                  <li key={c.name} title={c.description || (c.samples.length ? `e.g. ${c.samples.join(', ')}` : '')}>
                    <code>{c.name}</code>
                    <span className="muted small">{c.type}</span>
                    {c.pk && <span className="badge key">PK</span>}
                    {c.references && <span className="badge fk">→ {c.references}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export function CustomDbPanel({ onCreate }: { onCreate: (name: string, schema: string, seed: string) => Promise<void> }) {
  const [name, setName] = useState('my_db')
  const [schema, setSchema] = useState('CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, major TEXT);\nCREATE TABLE grades (student_id INTEGER REFERENCES students(id), course TEXT, grade REAL);')
  const [seed, setSeed] = useState("INSERT INTO students VALUES (1, 'Ana', 'CS'), (2, 'Lee', 'Math');\nINSERT INTO grades VALUES (1, 'DB', 3.7), (2, 'DB', 3.1), (1, 'AI', 4.0);")
  const [status, setStatus] = useState('')
  return (
    <form className="customDb" onSubmit={async (e) => {
      e.preventDefault()
      setStatus('Creating…')
      try {
        await onCreate(name, schema, seed)
        setStatus('Created and selected.')
      } catch (err) {
        setStatus((err as Error).message)
      }
    }}>
      <p className="muted small">Practise on your own tables. Scripts run in an isolated database; ATTACH, PRAGMA and extensions are blocked.</p>
      <label>Name<input value={name} onChange={(e) => setName(e.target.value)} pattern="[A-Za-z0-9_-]{1,64}" required /></label>
      <label>CREATE TABLE statements<textarea className="mono" rows={5} value={schema} onChange={(e) => setSchema(e.target.value)} /></label>
      <label>INSERT statements (optional)<textarea className="mono" rows={4} value={seed} onChange={(e) => setSeed(e.target.value)} /></label>
      <button className="primary full" type="submit">Create database</button>
      {status && <p className="small">{status}</p>}
    </form>
  )
}
