// Chain of Thought (shadcn AI Elements, built on Trace as the docs describe): one collapsible summary of Luna's work,
// open while she researches, folded once she answers. Inside, every step as she takes it: thought summaries
// (Reasoning), searches and page reads (Tool, input to output), each found page a link to the source viewer.
import * as React from 'react'
import { BookOpenIcon, BrainIcon, CircleAlertIcon, ImageIcon, LibraryIcon, SearchIcon, WrenchIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { CodeBlock, CodeBlockContent } from '@/components/ui/code-block'
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from '@/components/ui/trace'
import type { Bridge, Source, Step } from './types'

const ICON = { thought: BrainIcon, search: SearchIcon, read: BookOpenIcon, view: ImageIcon, catalogue: LibraryIcon, tool: WrenchIcon }

function seconds(a: number, b: number) {
  const s = Math.max(0, (b - a) / 1000)
  return s < 10 ? s.toFixed(1) + ' s' : Math.round(s) + ' s'
}

// a ticking clock while something runs
function useNow(running: boolean) {
  const [now, setNow] = React.useState(() => Date.now())
  React.useEffect(() => {
    if (!running) return
    const id = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(id)
  }, [running])
  return running ? now : 0
}

// open while running, fold when finished, unless the reader has toggled it (the Reasoning pattern's rule)
function useAutoOpen(running: boolean) {
  const [open, setOpen] = React.useState(running)
  const touched = React.useRef(false)
  React.useEffect(() => {
    if (!touched.current) setOpen(running)
  }, [running])
  const onOpenChange = (o: boolean) => {
    touched.current = true
    setOpen(o)
  }
  return { open, onOpenChange }
}

function PageLine({ s, bridge, quote }: { s: Source; bridge: Bridge; quote?: string }) {
  const { t } = bridge
  return (
    <button type="button" onClick={() => bridge.openSource(s.n, quote)} title={s.title}>
      <TraceLine>
        <Badge variant="secondary" className="h-4.5 min-w-4.5 px-1.5 tabular-nums">{s.n}</Badge>
        <TraceTitle className="text-foreground/80">{s.ref || s.title}</TraceTitle>
        {s.page != null && <TraceDescription>{t('p. {p}', { p: String(s.page) })}</TraceDescription>}
        {typeof s.relevance === 'number' && (
          <span className="lui-rel" aria-label={t('relevance {r}', { r: Math.round(s.relevance * 100) + ' %' })}>
            <i style={{ width: `${Math.round(Math.max(0.04, Math.min(1, s.relevance)) * 100)}%` }} />
          </span>
        )}
      </TraceLine>
    </button>
  )
}

function stepTitle(step: Step, t: Bridge['t']) {
  const q = String(step.args?.query ?? '')
  const running = step.status === 'running'
  switch (step.kind) {
    case 'thought': {
      const head = (step.text ?? '').match(/^\s*\*\*(.+?)\*\*/)
      return head ? head[1] : running ? t('Thinking') : t('Thought')
    }
    case 'search':
      return running ? t('Searching the guidelines for “{q}”', { q }) : t('Searched the guidelines for “{q}”', { q })
    case 'read': {
      const p = step.pages?.[0]
      if (!p) return running ? t('Reading the full page') : t('Read the full page')
      const what = `${p.ref || p.title}${p.page != null ? ', ' + t('p. {p}', { p: String(p.page) }) : ''}`
      const more = (step.pages?.length ?? 1) - 1
      return (running ? t('Reading {what}', { what }) : t('Read {what}', { what })) + (more > 0 ? ` +${more}` : '')
    }
    case 'view':
      return running ? t('Looking at the page image') : t('Looked at the page image')
    case 'catalogue':
      return running ? t('Checking which guidelines exist') : t('Checked which guidelines exist')
    default:
      return step.name ?? 'Tool'
  }
}

function ThoughtBody({ text }: { text: string }) {
  // Gemini's summaries start with a **heading**; the trigger shows it, the body the rest
  const body = text.replace(/^\s*\*\*(.+?)\*\*\s*/, '').replace(/\*\*(.+?)\*\*/g, '$1').trim()
  return <p className="whitespace-pre-wrap text-[13px] leading-relaxed">{body || '…'}</p>
}

function StepItem({ step, bridge }: { step: Step; bridge: Bridge }) {
  const { t } = bridge
  const Icon = step.status === 'error' ? CircleAlertIcon : ICON[step.kind]
  const running = step.status === 'running'
  const { open, onOpenChange } = useAutoOpen(step.kind === 'thought' && running)
  const status = running ? 'running' : step.status === 'error' ? 'error' : undefined
  const hits = step.hits ?? []
  const pages = step.pages ?? []
  const expandable = step.kind === 'thought' ? !!step.text : step.kind === 'search' || pages.length > 0
  const line = (
    <TraceLine status={status}>
      <TraceIcon><Icon /></TraceIcon>
      <TraceTitle>{stepTitle(step, t)}</TraceTitle>
      {step.kind === 'search' && !running && <TraceDescription>{t('{n} pages', { n: hits.length })}</TraceDescription>}
      {step.t1 && step.kind !== 'thought' && <TraceDescription className="max-sm:hidden">{seconds(step.t0, step.t1)}</TraceDescription>}
    </TraceLine>
  )
  if (!expandable) return <div>{line}</div>
  return (
    <TraceCollapsible open={open} onOpenChange={onOpenChange}>
      <TraceCollapsibleTrigger>{line}</TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        {step.kind === 'thought' && <ThoughtBody text={step.text ?? ''} />}
        {step.kind === 'search' && (
          <div className="flex flex-col gap-2.5">
            <CodeBlock size="sm" className="bg-muted/40">
              <CodeBlockContent wrap className="max-h-28 text-[11.5px]">
                {JSON.stringify({ query: step.args?.query, ...(step.args?.guideline ? { guideline: step.args.guideline } : {}) }, null, 1)}
              </CodeBlockContent>
            </CodeBlock>
            {step.mode && (
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <Badge variant="outline">{step.mode === 'bm25' ? t('keyword search') : t('EmbeddingGemma 2 + reranker')}</Badge>
                <span>{t('ranked by meaning, then re-read by a second model')}</span>
              </div>
            )}
            {running && !hits.length && <span className="shimmer text-xs">{t('Searching')}</span>}
            {hits.length > 0 && <Trace className="gap-1.5">{hits.map(s => <PageLine key={s.n} s={s} bridge={bridge} quote={s.snippet ?? undefined} />)}</Trace>}
          </div>
        )}
        {(step.kind === 'read' || step.kind === 'view') && (
          <Trace className="gap-1.5">{pages.map(s => <PageLine key={s.n} s={s} bridge={bridge} />)}</Trace>
        )}
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

export function ChainOfThought({ steps, running, answering, bridge }: { steps: Step[]; running: boolean; answering: boolean; bridge: Bridge }) {
  const { t } = bridge
  const working = running && !answering
  const now = useNow(running)
  const { open, onOpenChange } = useAutoOpen(working)
  if (!steps.length) return null
  const t0 = steps[0].t0
  const t1 = Math.max(...steps.map(s => s.t1 ?? s.t0))
  const tools = steps.filter(s => s.kind !== 'thought').length
  const title = working
    ? t('Researching in the guidelines')
    : t('Researched in {n} steps', { n: tools || steps.length })
  return (
    <Trace className="lui-cot">
      <TraceCollapsible open={open} onOpenChange={onOpenChange}>
        <TraceCollapsibleTrigger>
          <TraceLine status={working ? 'running' : undefined}>
            <TraceIcon><BrainIcon /></TraceIcon>
            <TraceTitle className="font-medium">{title}</TraceTitle>
            <TraceDescription>{seconds(t0, working ? now || t1 : t1)}</TraceDescription>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent keepMounted>
          <Trace className="gap-2.5">
            {steps.map(s => <StepItem key={s.id} step={s} bridge={bridge} />)}
          </Trace>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}
