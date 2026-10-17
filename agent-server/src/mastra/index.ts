// The agent server: Mastra with one route, POST /luna/chat, that streams TIB Chat's chat events (server-sent events
// in the app's own format: text, tool_call, sources, read, done, error), so the browser code stays unchanged.
//
// The loop is Mastra's (on the AI SDK): the model calls tools as it sees fit, several per step, and the run ends when
// the model answers without calling a tool. No step limit: Mastra applies stepCountIs(5) when neither maxSteps nor
// stopWhen is given, so stopWhen is a condition that never fires (the AI SDK's isLoopFinished()). An evaluation can
// still pass max_steps.
import { Mastra } from '@mastra/core'
import { RequestContext } from '@mastra/core/request-context'
import { registerApiRoute } from '@mastra/core/server'
import { luna } from './agents/luna.ts'

// the AI SDK's isLoopFinished(): never stop on a condition, only when the model is done
const loopFinished = () => false
const DEBUG = process.env.DEBUG_CHUNKS === '1'  // log every stream chunk (development)
const TOKEN = process.env.AGENT_TOKEN ?? ''  // local development; on Cloud Run the service is private (IAM)

type ChatBody = {
  messages: Array<{ role: string; text: string }>
  system: string
  run: string
  max_steps?: number
  thinking?: 'minimal' | 'low' | 'medium' | 'high'
}

const chatRoute = registerApiRoute('/luna/chat', {
  method: 'POST',
  handler: async c => {
    if (TOKEN && c.req.header('X-Agent-Token') !== TOKEN) return c.json({ error: 'Agent token required' }, 401)
    const body = (await c.req.json()) as ChatBody
    const maxSteps = Number(body.max_steps) > 0 ? Number(body.max_steps) : undefined
    const messages = (body.messages ?? []).slice(-12)
      .filter(m => m.text)
      .map(m => m.role === 'assistant'
        ? { role: 'assistant' as const, content: String(m.text).slice(0, 8000) }
        : { role: 'user' as const, content: String(m.text).slice(0, 8000) })
    const question = [...messages].reverse().find(m => m.role === 'user')?.content ?? ''
    const requestContext = new RequestContext()
    requestContext.set('system', body.system)
    requestContext.set('runId', body.run)
    requestContext.set('question', question.slice(0, 1000))

    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      async start(controller) {
        const send = (ev: Record<string, unknown>) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`))
        try {
          const out = await luna.stream(messages, {
            requestContext,
            ...(maxSteps ? { maxSteps } : { stopWhen: loopFinished }),
            toolCallConcurrency: 4,
            providerOptions: { vertex: { thinkingConfig: { thinkingLevel: body.thinking ?? 'low' } } },
          })
          for await (const chunk of out.fullStream as unknown as AsyncIterable<{ type: string; payload: any }>) {
            if (DEBUG) console.error(`[${body.run}] ${chunk.type}`, JSON.stringify(chunk.payload ?? {}).slice(0, 300))
            if (chunk.type === 'text-delta') send({ type: 'text', delta: chunk.payload.text })
            else if (chunk.type === 'tool-call') send({ type: 'tool_call', name: chunk.payload.toolName, args: chunk.payload.args })
            else if (chunk.type === 'tool-result') for (const ev of chunk.payload.result?.ui ?? []) send(ev)
            else if (chunk.type === 'tool-error') send({ type: 'tool_error', name: chunk.payload.toolName })
            else if (chunk.type === 'error') send({ type: 'error', message: 'LLM unavailable' })
          }
          send({ type: 'done' })
        } catch (e) {
          send({ type: 'error', message: `Agent error (${(e as Error).name})` })
        } finally {
          controller.close()
        }
      },
    })
    return new Response(stream, { headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' } })
  },
})

export const mastra = new Mastra({
  agents: { luna },
  server: {
    host: '0.0.0.0',
    apiRoutes: [chatRoute],
  },
})
