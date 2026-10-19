// The claim guard (app/claimguard.py) while the answer streams: one line between the agent's steps and the answer.
// While it runs it shimmers with live counts; afterwards it opens to every decision: a statement cited with the page
// that supports it, kept (no claim, a conclusion from cited statements, or uncertain evidence), or removed (struck
// through: a claim no retrieved page supports).
import * as React from 'react'
import { CheckCheckIcon, ShieldCheckIcon } from 'lucide-react'

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
import type { Bridge, Verify } from './types'

const WHY: Record<string, string> = {
  'no claim': 'no claim',
  'follows from the answer': 'follows from the cited statements',
  uncertain: 'evidence uncertain, left as written',
  unchecked: 'not checked',
  'unsupported claim': 'no retrieved page supports it',
}

export function VerifyLine({ v, bridge }: { v: Verify; bridge: Bridge }) {
  const { t } = bridge
  const running = v.status === 'running'
  const [open, setOpen] = React.useState(false)
  const counts = [
    v.cited ? t('{n} cited', { n: v.cited }) : '',
    v.removed ? t('{n} removed', { n: v.removed }) : '',
    v.kept ? t('{n} kept', { n: v.kept }) : '',
  ].filter(Boolean).join(' · ')
  const line = (
    <TraceLine status={running ? 'running' : undefined}>
      <TraceIcon>{running ? <ShieldCheckIcon /> : <CheckCheckIcon />}</TraceIcon>
      <TraceTitle className={running ? 'shimmer' : undefined}>
        {running ? t('Checking each statement against the sources') : t('{n} statements checked against the sources', { n: v.checked })}
      </TraceTitle>
      {counts && <TraceDescription>{counts}</TraceDescription>}
    </TraceLine>
  )
  if (running || !v.items.length) return <Trace className="lui-verify">{line}</Trace>
  return (
    <Trace className="lui-verify">
      <TraceCollapsible open={open} onOpenChange={setOpen}>
        <TraceCollapsibleTrigger>{line}</TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <ul className="lui-verify-items">
            {v.items.map((it, i) => (
              <li key={i} className={'v-' + it.action}>
                <span className="v-tag">{it.action === 'cite' ? `[${it.n}]` : it.action === 'remove' ? t('removed') : t('kept')}</span>
                <span className="v-text">{it.text}</span>
                <span className="v-why">{t(WHY[it.why] ?? it.why)}</span>
              </li>
            ))}
          </ul>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}
