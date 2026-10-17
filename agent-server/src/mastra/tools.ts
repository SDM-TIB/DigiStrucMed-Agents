// Luna's tools. Retrieval runs in the TIB Chat app (app/search.py, app/luna.py: the evaluated EmbeddingGemma 2 +
// clef-flash search); these tools call its token-guarded endpoints /api/agent/tools/*. Each endpoint answers with
// `model` (what the model reads) and `ui` (sources and read events for the browser). toModelOutput keeps the ui
// events out of the model's context; the chat route forwards them to the browser.
import { createTool } from '@mastra/core/tools'
import { z } from 'zod'

const APP_URL = (process.env.APP_URL ?? '').replace(/\/$/, '')
const AGENT_TOKEN = process.env.AGENT_TOKEN ?? ''

type ToolReply = { model: unknown; ui: Array<Record<string, unknown>>; images?: Array<{ mediaType: string; data: string }> }

// On Cloud Run: an identity token of this service's account for the app's url (the app accepts its own account).
// Locally: the shared AGENT_TOKEN.
let idToken: { value: string; until: number } | undefined
async function authHeaders(): Promise<Record<string, string>> {
  if (AGENT_TOKEN) return { 'X-Agent-Token': AGENT_TOKEN }
  if (!idToken || Date.now() > idToken.until) {
    const url = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=' +
      encodeURIComponent(APP_URL)
    const res = await fetch(url, { headers: { 'Metadata-Flavor': 'Google' }, signal: AbortSignal.timeout(5_000) })
    if (!res.ok) throw new Error(`identity token: HTTP ${res.status}`)
    idToken = { value: await res.text(), until: Date.now() + 45 * 60_000 }
  }
  return { Authorization: `Bearer ${idToken.value}` }
}

async function callApp(tool: string, body: Record<string, unknown>): Promise<ToolReply> {
  const res = await fetch(`${APP_URL}/api/agent/tools/${tool}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60_000),
  })
  if (!res.ok) throw new Error(`app tool ${tool}: HTTP ${res.status}`)
  return (await res.json()) as ToolReply
}

const runOf = (ctx: { requestContext?: { get: (k: string) => unknown } }) => ({
  run: String(ctx.requestContext?.get('runId') ?? 'default'),
  question: String(ctx.requestContext?.get('question') ?? ''),
})

const toModel = (out: unknown) => ({ type: 'json' as const, value: (out as ToolReply).model as never })

export const searchGuidelines = createTool({
  id: 'search_guidelines',
  description:
    'Semantic search over the guideline pages (EmbeddingGemma 2, each candidate page judged by a relevance model). ' +
    'Returns numbered sources (guideline, author/year, page, relevance 0-1, page windows) and facets: the guidelines ' +
    'where the likely relevant pages cluster.',
  inputSchema: z.object({
    query: z.string().describe('What to find, as a full phrase with the clinical terms and the population, ' +
      'e.g. "class of recommendation for beta-blockers in patients with HFrEF"'),
    guideline: z.string().optional().describe('Optional: search only in this guideline, given by its words ' +
      '(e.g. "2021 ESC heart failure") or its number from list_guidelines'),
    top_k: z.number().int().min(3).max(8).optional().describe('Number of sources, 3-8 (default 5)'),
  }),
  execute: async (input, ctx) => callApp('search', { ...input, ...runOf(ctx) }),
  toModelOutput: toModel,
})

export const readPage = createTool({
  id: 'read_page',
  description: 'Read up to three whole pages returned as numbered sources, as the original source: the OCR text in ' +
      'Markdown with the HTML tables (rows and columns as in the guideline, signs like < and > kept).',
  inputSchema: z.object({
    sources: z.array(z.number().int()).min(1).max(3).describe('Source numbers from earlier search results'),
  }),
  execute: async (input, ctx) => callApp('read', { ...input, ...runOf(ctx) }),
  toModelOutput: toModel,
})

export const listGuidelines = createTool({
  id: 'list_guidelines',
  description: 'The guideline catalogue: number, title, author/year, language and pages of each document, ' +
    'optionally only those matching a topic. Use it to find the guideline to search in.',
  inputSchema: z.object({
    topic: z.string().optional().describe('Optional words to match in titles, e.g. "ventricular arrhythmias"'),
  }),
  execute: async (input, ctx) => callApp('catalogue', { ...input, ...runOf(ctx) }),
  toModelOutput: toModel,
})

// The rendered PDF page as an image (WebP), for what the OCR text may have lost: figures, footnotes, shifted table
// cells. The model gets the page list as text and the images as image parts.
export const viewPage = createTool({
  id: 'view_page',
  description: 'Look at up to two pages returned as numbered sources as images: the rendered pages of the original PDF ' +
    '(figures, footnotes and table layout as printed).',
  inputSchema: z.object({
    sources: z.array(z.number().int()).min(1).max(2).describe('Source numbers from earlier search results'),
  }),
  execute: async (input, ctx) => callApp('view', { ...input, ...runOf(ctx) }),
  toModelOutput: (out: unknown) => {
    const reply = out as ToolReply
    return {
      type: 'content' as const,
      value: [
        { type: 'text' as const, text: JSON.stringify(reply.model) },
        ...(reply.images ?? []).map(im => ({ type: 'image-data' as const, data: im.data, mediaType: im.mediaType })),
      ],
    }
  },
})
