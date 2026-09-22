import { useMemo } from 'react'
import CodeMirror, { EditorView, keymap } from '@uiw/react-codemirror'
import { SQLite, sql } from '@codemirror/lang-sql'
import { Prec } from '@codemirror/state'
import type { Table } from '../api'

type Props = {
  value: string
  onChange: (v: string) => void
  tables: Table[]
  onRun: () => void
  dark: boolean
}

/** CodeMirror 6 editor with schema-aware autocomplete and Ctrl/Cmd+Enter to run. */
export function SqlEditor({ value, onChange, tables, onRun, dark }: Props) {
  const extensions = useMemo(() => {
    const schema = Object.fromEntries(tables.map((t) => [t.name, t.columns.map((c) => c.name)]))
    return [
      sql({ dialect: SQLite, schema, upperCaseKeywords: true }),
      EditorView.lineWrapping,
      Prec.highest(keymap.of([{ key: 'Mod-Enter', run: () => (onRun(), true) }])),
    ]
  }, [tables, onRun])

  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      extensions={extensions}
      theme={dark ? 'dark' : 'light'}
      minHeight="150px"
      maxHeight="320px"
      basicSetup={{ lineNumbers: true, foldGutter: false, highlightActiveLine: true }}
      aria-label="SQL editor"
    />
  )
}
