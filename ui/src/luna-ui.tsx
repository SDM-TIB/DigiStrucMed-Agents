// The AI Elements island of TIB Chat. app/static/js/app.js keeps the chat (bubbles, streaming text and its blur-in,
// history, source viewer) and hands each assistant message to window.LunaUI.message(), which renders the agent's
// work with shadcn AI Elements: Chain of Thought with Reasoning and Tool steps while Luna researches, Sources under
// the answer, Inline Citation hover cards on its [n] marks.
import './index.css'

import { createPortal, flushSync } from 'react-dom'
import { createRoot, type Root } from 'react-dom/client'

import { Answer } from './elements/answer'
import { ChainOfThought } from './elements/chain-of-thought'
import { InlineCitation } from './elements/inline-citation'
import { Sources } from './elements/sources'
import type { Bridge, SearchStage, Source, Step, StepKind } from './elements/types'

type ChatEvent = {
  type: string
  name?: string
  args?: Record<string, unknown>
  items?: Source[]
  query?: string
  mode?: string
  item?: Source
  delta?: string
  stage?: SearchStage['stage']
}

const KIND: Record<string, StepKind> = { search_guidelines: 'search', read_page: 'read', view_page: 'view', list_guidelines: 'catalogue' }

function island(el: HTMLElement) {
  el.classList.add('lui')
  return createRoot(el)
}

class MessageUI {
  private steps: Step[] = []
  private nextId = 1
  private running = true
  private answering = false
  private root: Root
  private sourcesRoot?: Root
  private citeRoot?: Root
  private citeHosts: HTMLElement[] = []
  private bridge: Bridge
  constructor(stepsEl: HTMLElement, bridge: Bridge) {
    this.root = island(stepsEl)
    this.bridge = bridge
  }

  private render() {
    this.root.render(
      <ChainOfThought steps={this.steps.map(s => ({ ...s }))} running={this.running} answering={this.answering} bridge={this.bridge} />,
    )
  }

  // finish what is still running, except the kinds named
  private settle(keep: StepKind[] = []) {
    const now = Date.now()
    for (const s of this.steps) if (s.status === 'running' && !keep.includes(s.kind)) Object.assign(s, { status: 'done', t1: now })
  }

  private last(pred: (s: Step) => boolean) {
    for (let i = this.steps.length - 1; i >= 0; i--) if (pred(this.steps[i])) return this.steps[i]
    return undefined
  }

  /** one chat event; returns whether the island showed it */
  event(ev: ChatEvent): boolean {
    const now = Date.now()
    switch (ev.type) {
      case 'reasoning': {
        const open = this.last(s => s.kind === 'thought' && s.status === 'running')
        if (open && this.steps[this.steps.length - 1] === open) open.text = (open.text ?? '') + (ev.delta ?? '')
        else {
          this.settle(['search'])
          this.steps.push({ id: this.nextId++, kind: 'thought', status: 'running', text: ev.delta ?? '', t0: now })
        }
        break
      }
      case 'tool_call':
        this.settle(['search', 'read', 'view', 'catalogue', 'tool']) // thoughts end; parallel calls keep running
        this.steps.push({ id: this.nextId++, kind: KIND[ev.name ?? ''] ?? 'tool', name: ev.name, args: ev.args ?? {}, status: 'running', t0: now })
        break
      case 'sources': {
        const s = this.last(x => x.kind === 'search' && !x.hits && String(x.args?.query ?? '') === String(ev.query ?? ''))
          ?? this.steps.find(x => x.kind === 'search' && !x.hits)
        if (!s) return false
        Object.assign(s, { hits: ev.items ?? [], mode: ev.mode, status: 'done', t1: now })
        break
      }
      case 'search_progress': {
        // the app's search reports its stages while it runs (app/search.py progress, app/live.py)
        const s = this.last(x => x.kind === 'search' && x.status === 'running' && String(x.args?.query ?? '') === String(ev.query ?? ''))
          ?? this.last(x => x.kind === 'search' && x.status === 'running')
        if (!s || !ev.stage) return false
        s.progress = { ...(s.progress ?? {}), [ev.stage]: ev as unknown as SearchStage }
        break
      }
      case 'read': {
        const s = this.last(x => (x.kind === 'read' || x.kind === 'view') && x.status === 'running')
        if (!s || !ev.item) return false
        s.pages = [...(s.pages ?? []), ev.item]
        break
      }
      case 'tool_error': {
        const s = this.last(x => x.name === ev.name && x.status === 'running')
        if (s) Object.assign(s, { status: 'error', t1: now })
        break
      }
      case 'text':
        if (this.answering) return true
        this.answering = true
        this.settle()
        break
      default:
        return false
    }
    this.render()
    return true
  }

  /** the answer is complete (or failed) */
  done() {
    this.running = false
    this.settle()
    for (const s of this.steps) if (s.kind === 'search' && !s.hits) s.hits = []
    this.render()
  }

  private answerRoot?: Root
  private sources_: Record<number, Source> = {}

  /** the answer text with Streamdown, into the app's .answer box. while streaming it renders each update; when final,
   * it keeps the blur running until the last words are sharp, then renders the plain text and resolves */
  answer(el: HTMLElement, raw: string, sources: Record<number, Source>, final = false): Promise<void> {
    if (!this.answerRoot) this.answerRoot = island(el)
    this.sources_ = sources
    const draw = (streaming: boolean) => this.answerRoot!.render(
      <Answer raw={raw} streaming={streaming} sources={this.sources_} bridge={this.bridge} />)
    if (!final) {
      draw(true)
      return Promise.resolve()
    }
    flushSync(() => draw(true))
    return new Promise(resolve => setTimeout(() => {
      flushSync(() => draw(false))
      resolve()
    }, 700))
  }

  /** an error replaces the answer: the app writes it into the box itself */
  releaseAnswer() {
    this.answerRoot?.unmount()
    this.answerRoot = undefined
  }

  /** Sources under the answer, into the app's .refs box */
  sources(el: HTMLElement, list: Source[]) {
    if (!list.length) return
    this.sourcesRoot ??= island(el)
    this.sourcesRoot.render(<Sources sources={list} bridge={this.bridge} />)
  }

  /** turn the answer's [n] buttons (app.js citeify) into Inline Citations, once the text is final */
  citations(answer: HTMLElement, sources: Record<number, Source>) {
    const cites = [...answer.querySelectorAll<HTMLButtonElement>('button.cite')].filter(b => !b.closest('.cite-host'))
    if (!cites.length) return
    const hosts = cites.map(b => {
      const host = document.createElement('span')
      host.className = 'cite-host'
      host.dataset.n = b.dataset.n
      b.replaceWith(host)
      return host
    })
    // the root lives inside the message, so its events reach the portals the usual way
    this.citeHosts.push(...hosts)
    if (!this.citeRoot) {
      const anchor = document.createElement('span')
      anchor.hidden = true
      answer.after(anchor)
      this.citeRoot = island(anchor)
    }
    this.citeRoot.render(
      <>{this.citeHosts.map((h, i) => createPortal(<InlineCitation n={Number(h.dataset.n)} source={sources[Number(h.dataset.n)]} bridge={this.bridge} />, h, String(i)))}</>,
    )
  }
}

const LunaUI = {
  version: 1,
  message: (stepsEl: HTMLElement, bridge: Bridge) => new MessageUI(stepsEl, bridge),
}

declare global {
  interface Window { LunaUI?: typeof LunaUI }
}
window.LunaUI = LunaUI
window.dispatchEvent(new Event('lunaui-ready'))
