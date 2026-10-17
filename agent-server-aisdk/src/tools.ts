// Luna's tools for the AI SDK 7 agent: the same three tools as agent-server/ (Mastra), calling the same token-guarded
// endpoints of the TIB Chat app (/api/agent/tools/*), where the evaluated retrieval runs (EmbeddingGemma 2 +
// clef-flash). Each endpoint answers { model, ui }; toModelOutput gives the model only `model`, the chat route
// forwards `ui` (sources and read events) to the browser. The run id and the user's question reach the tools
// through the AI SDK's typed tool context (contextSchema / toolsContext).
import { tool, type JSONValue } from 'ai'
import { z } from 'zod'

const APP_URL = (process.env.APP_URL ?? '').replace(/\/$/, '')
const AGENT_TOKEN = process.env.AGENT_TOKEN ?? ''  // local development; on Cloud Run an identity token is used

export type ToolReply = { model: unknown; ui: Array<Record<string, unknown>> }

// On Cloud Run: an identity token of this service's account for the app's url (the app accepts its own account).
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

async function callApp(name: string, body: Record<string, unknown>): Promise<ToolReply> {
  const res = await fetch(`${APP_URL}/api/agent/tools/${name}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60_000),
  })
  if (!res.ok) throw new Error(`app tool ${name}: HTTP ${res.status}`)
  return (await res.json()) as ToolReply
}

const runContext = z.object({ run: z.string(), question: z.string() })

export const tools = {
  search_guidelines: tool({
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
    contextSchema: runContext,
    execute: async (input, { context }) => callApp('search', { ...input, ...context }),
    toModelOutput: ({ output }) => ({ type: 'json', value: output.model as JSONValue }),
  }),
  read_page: tool({
    description: 'Read up to three whole pages returned as numbered sources, as the original source: the OCR text in ' +
      'Markdown with the HTML tables (rows and columns as in the guideline, signs like < and > kept).',
    inputSchema: z.object({
      sources: z.array(z.number().int()).min(1).max(3).describe('Source numbers from earlier search results'),
    }),
    contextSchema: runContext,
    execute: async (input, { context }) => callApp('read', { ...input, ...context }),
    toModelOutput: ({ output }) => ({ type: 'json', value: output.model as JSONValue }),
  }),
  list_guidelines: tool({
    description: 'The guideline catalogue: number, title, author/year, language and pages of each document, ' +
      'optionally only those matching a topic. Use it to find the guideline to search in.',
    inputSchema: z.object({
      topic: z.string().optional().describe('Optional words to match in titles, e.g. "ventricular arrhythmias"'),
    }),
    contextSchema: runContext,
    execute: async (input, { context }) => callApp('catalogue', { ...input, ...context }),
    toModelOutput: ({ output }) => ({ type: 'json', value: output.model as JSONValue }),
  }),
}
