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

export type SearchStage = {
  stage: 'embed' | 'compare' | 'rerank'
  status: 'running' | 'done' | 'error'
  ms?: number
  model?: string
  dims?: number
  texts?: number
  vectors?: number
  candidates?: number
  judged?: number
  of?: number
  pages?: Array<{ id: string; label: string }> // the candidate pages, in the order the vectors ranked them
  scores?: Record<string, number> // clef-flash's relevance per candidate page so far
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
  progress?: Record<string, SearchStage> // a search's stages as they happen (embed, compare, rerank)
  t0: number
  t1?: number
}

// the claim guard's progress (app/claimguard.py guard: 'verify' events)
export type Verify = {
  status: 'running' | 'done'
  checked: number
  kept: number
  cited: number
  removed: number
  items: Array<{ action: 'keep' | 'cite' | 'remove'; why: string; n?: number | null; text: string }>
}

// the english text is the key; the app translates it (static/js/i18n.json) and fills {placeholders}
export type Translate = (en: string, vars?: Record<string, string | number>) => string

export type Bridge = {
  t: Translate
  openSource: (n: number, quote?: string) => void
}
