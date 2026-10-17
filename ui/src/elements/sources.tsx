// Sources (shadcn AI Elements, built on Trace): "Used n sources" under an answer, unfolding to the cited guideline
// pages. Each opens the app's source viewer at that page. Open by default: the citations are the point of the answer.
import { ArrowUpRightIcon, FileTextIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
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
import type { Bridge, Source } from './types'

export function Sources({ sources, bridge }: { sources: Source[]; bridge: Bridge }) {
  const { t } = bridge
  // one line per page: an answer may cite the same page under one number only, but guard anyway
  const seen = new Set<string>()
  const list = sources.filter(s => {
    const key = s.page_id ?? String(s.n)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  if (!list.length) return null
  return (
    <Trace className="lui-sources">
      <TraceCollapsible defaultOpen>
        <TraceCollapsibleTrigger>
          <TraceLine>
            <TraceIcon><FileTextIcon /></TraceIcon>
            <TraceTitle className="font-medium">{list.length === 1 ? t('Used 1 source') : t('Used {n} sources', { n: list.length })}</TraceTitle>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent keepMounted>
          <Trace className="gap-1.5">
            {list.map(s => (
              <button key={s.n} type="button" className="lui-src" data-n={s.n} onClick={() => bridge.openSource(s.n)} title={s.title}>
                <TraceLine>
                  <Badge className="h-4.5 min-w-4.5 px-1.5 tabular-nums">{s.n}</Badge>
                  <TraceTitle className="text-foreground/85">{s.ref || s.title}</TraceTitle>
                  {s.page != null && <TraceDescription>{t('p. {p}', { p: String(s.page) })}</TraceDescription>}
                  <TraceIcon><ArrowUpRightIcon /></TraceIcon>
                </TraceLine>
              </button>
            ))}
          </Trace>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}
