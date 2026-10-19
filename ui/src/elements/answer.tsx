// The answer text, streamed with Streamdown (https://streamdown.ai): incomplete Markdown is closed while it streams,
// so no stray ** or half links flash up; each new word blurs in with the chat's own animation (sd-lunaBlur in
// index.css, the same curve as the old .blur-in); math through @streamdown/math (KaTeX); every [n] becomes an
// Inline Citation that works while the answer is still streaming; code blocks (Lino's storyboard JSON) through
// @streamdown/code with highlighting and a copy button.
import { code } from '@streamdown/code'
import { createMathPlugin } from '@streamdown/math'
import * as React from 'react'
import { defaultComponents, Streamdown } from 'streamdown'

import { InlineCitation } from './inline-citation'
import type { Bridge, Source } from './types'

// single $ for inline math, as the models write it ($\rightarrow$); a $ right before a digit stays a dollar sign
const math = createMathPlugin({ singleDollarTextMath: true })

const CITE = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\](?!\()/g
const CITE_HREF = '#cite-'

/** the model's text as Streamdown gets it: [n] as citation links, currency kept literal, a citation still being
 * typed at the end held back until it is complete. fenced code is left as it is */
export function prepare(raw: string, streaming: boolean) {
  let text = streaming ? raw.replace(/\[\d{0,2}(?:\s*,\s*\d{0,2})*$/, '') : raw
  const parts = text.split(/(```[\s\S]*?(?:```|$))/)
  text = parts.map((p, i) => i % 2 ? p : p
    .replace(/\$(?=\d)/g, '\\$')
    .replace(CITE, (_, g: string) => (g.match(/\d+/g) ?? []).map(n => `[${n}](${CITE_HREF}${n})`).join(''))).join('')
  return text
}

export function Answer({ raw, streaming, sources, bridge }: { raw: string; streaming: boolean; sources: Record<number, Source>; bridge: Bridge }) {
  const md = React.useMemo(() => prepare(raw, streaming), [raw, streaming])
  // the source list grows while Luna searches; a ref keeps the components stable for Streamdown's memoisation
  const srcRef = React.useRef(sources)
  srcRef.current = sources
  const components = React.useMemo(() => ({
    a: (props: React.ComponentProps<'a'> & { node?: unknown }) => {
      const href = props.href ?? ''
      if (href.startsWith(CITE_HREF)) {
        const n = Number(href.slice(CITE_HREF.length))
        return <InlineCitation n={n} source={srcRef.current[n]} bridge={bridge} />
      }
      return React.createElement(defaultComponents.a as React.ElementType, props)
    },
  }), [bridge])
  return (
    <Streamdown
      className="lui-answer"
      plugins={{ math, code }}
      components={components}
      animated={{ animation: 'lunaBlur', duration: 850, easing: 'cubic-bezier(.2,.75,.25,1)' }}
      isAnimating={streaming}
    >
      {md}
    </Streamdown>
  )
}
