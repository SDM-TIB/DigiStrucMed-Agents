// The AI SDK 7 agent server: POST /luna/chat streams TIB Chat's chat events (server-sent events in the app's own
// format: text, tool_call, sources, read, done, error), the same contract as agent-server/ (Mastra), so the app can use
// either harness. GET /health for probes. Node runs this TypeScript directly (type stripping, Node >= 22.18).
import { createServer, type IncomingMessage } from 'node:http'
import type { ModelMessage } from 'ai'
import { lunaAgent } from './agent.ts'

const PORT = Number(process.env.PORT ?? 8080)
const TOKEN = process.env.AGENT_TOKEN ?? ''  // local development; on Cloud Run the service is private (IAM)

type ChatBody = {
  messages: Array<{ role: string; text: string }>
  system: string
  run: string
  max_steps?: number
  thinking?: 'minimal' | 'low' | 'medium' | 'high'
}

async function readJson(req: IncomingMessage): Promise<ChatBody> {
  const chunks: Buffer[] = []
  for await (const c of req) chunks.push(c as Buffer)
  return JSON.parse(Buffer.concat(chunks).toString('utf8')) as ChatBody
}

createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' }).end('{"status":"ok"}')
    return
  }
  if (req.method !== 'POST' || req.url !== '/luna/chat') {
    res.writeHead(404).end()
    return
  }
  if (TOKEN && req.headers['x-agent-token'] !== TOKEN) {
    res.writeHead(401, { 'Content-Type': 'application/json' }).end('{"error":"Agent token required"}')
    return
  }
  const body = await readJson(req)
  const messages: ModelMessage[] = (body.messages ?? []).slice(-12)
    .filter(m => m.text)
    .map(m => m.role === 'assistant'
      ? { role: 'assistant', content: String(m.text).slice(0, 8000) }
      : { role: 'user', content: String(m.text).slice(0, 8000) })
  const question = [...messages].reverse().find(m => m.role === 'user')?.content

  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
  const send = (ev: Record<string, unknown>) => res.write(`data: ${JSON.stringify(ev)}\n\n`)
  try {
    const agent = lunaAgent({
      system: body.system,
      run: String(body.run ?? 'default'),
      question: String(question ?? '').slice(0, 1000),
      maxSteps: Number(body.max_steps) > 0 ? Number(body.max_steps) : undefined,
      thinking: body.thinking,
    })
    const result = await agent.stream({ messages })
    for await (const part of result.fullStream) {
      if (part.type === 'text-delta') send({ type: 'text', delta: part.text })
      else if (part.type === 'tool-call') send({ type: 'tool_call', name: part.toolName, args: part.input })
      else if (part.type === 'tool-result') for (const ev of (part.output as { ui?: Array<Record<string, unknown>> })?.ui ?? []) send(ev)
      else if (part.type === 'tool-error') send({ type: 'tool_error', name: part.toolName })
      else if (part.type === 'error') send({ type: 'error', message: 'LLM unavailable' })
    }
    send({ type: 'done' })
  } catch (e) {
    send({ type: 'error', message: `Agent error (${(e as Error).name})` })
  } finally {
    res.end()
  }
}).listen(PORT, '0.0.0.0', () => console.log(`luna agent (AI SDK 7) on :${PORT}`))
