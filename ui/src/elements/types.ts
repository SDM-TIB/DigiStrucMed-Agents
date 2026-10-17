// The chat's agent events (app/luna.py, agent-server/) as the island keeps them: one Step per tool call or thought.

export type Source = {
  n: number
  title: string
  ref?: string | null
  page?: number | string | null
  page_id?: string
  snippet?: string | null
  relevance?: number
}

export type StepKind = 'thought' | 'search' | 'read' | 'view' | 'catalogue' | 'tool'

export type Step = {
  id: number
  kind: StepKind
  status: 'running' | 'done' | 'error'
  name?: string
  args?: Record<string, unknown>
  text?: string // a thought's summary
  hits?: Source[] // what a search found
  mode?: string // the search mode that served it
  pages?: Source[] // what read_page / view_page opened
  t0: number
  t1?: number
}

// the english text is the key; the app translates it (static/js/i18n.json) and fills {placeholders}
export type Translate = (en: string, vars?: Record<string, string | number>) => string

export type Bridge = {
  t: Translate
  openSource: (n: number, quote?: string) => void
}
