// Inline Citation (shadcn AI Elements: a Hover Card on the citation): hovering [n] in an answer previews the cited
// guideline page (title, page, the passage the search matched, the rendered PDF page). A click or a tap still opens
// the app's source viewer, as before; touch screens have no hover, so there the tap is the way in.
import { ArrowUpRightIcon } from 'lucide-react'
import * as React from 'react'

import { Button } from '@/components/ui/button'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import type { Bridge, Source } from './types'

function PageImage({ id, alt }: { id: string; alt: string }) {
  const [state, setState] = React.useState<'loading' | 'ok' | 'none'>('loading')
  if (state === 'none') return null
  return (
    <div className="relative mt-1 overflow-hidden rounded-md bg-muted ring-1 ring-foreground/10" style={{ aspectRatio: '1100 / 1464', maxHeight: 220 }}>
      {state === 'loading' && <div className="absolute inset-0 shimmer bg-muted" />}
      <img src={`/api/page-image/${encodeURIComponent(id)}`} alt={alt} loading="lazy" decoding="async"
           className="h-full w-full object-cover object-top" onLoad={() => setState('ok')} onError={() => setState('none')} />
    </div>
  )
}

export function InlineCitation({ n, source, bridge }: { n: number; source?: Source; bridge: Bridge }) {
  const { t } = bridge
  const trigger = <button type="button" className="cite cite-in" data-n={n} aria-label={t('Source {n}', { n })} />
  if (!source) return React.cloneElement(trigger, {}, n)
  const where = [source.ref, source.page != null ? t('p. {p}', { p: String(source.page) }) : ''].filter(Boolean).join(' · ')
  return (
    <HoverCard>
      <HoverCardTrigger delay={120} closeDelay={120} render={trigger}>{n}</HoverCardTrigger>
      <HoverCardContent side="top" className="flex w-72 flex-col gap-1.5 max-sm:hidden">
        <div className="text-[13px] leading-snug font-semibold text-foreground">{source.title}</div>
        {where && <div className="text-xs text-muted-foreground">{where}</div>}
        {source.snippet && (
          <blockquote className="line-clamp-4 border-l-2 border-primary/40 pl-2 text-xs leading-relaxed text-foreground/80">
            {source.snippet}
          </blockquote>
        )}
        {source.page_id && <PageImage id={source.page_id} alt={where || source.title} />}
        <Button variant="secondary" size="sm" className="mt-1 w-fit" onClick={() => bridge.openSource(n)}>
          {t('Open page')} <ArrowUpRightIcon data-icon="inline-end" />
        </Button>
      </HoverCardContent>
    </HoverCard>
  )
}
