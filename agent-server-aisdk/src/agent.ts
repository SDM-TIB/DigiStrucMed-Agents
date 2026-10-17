// Luna as an AI SDK 7 ToolLoopAgent (the second agent harness next to agent-server/, Mastra). Gemini on Vertex AI.
// One agent per request: its instructions come from the TIB Chat app (app/luna.py, one prompt source) and its tools'
// typed context (run id, the user's question) belongs to that request. No step limit unless an evaluation asks for
// one: isLoopFinished() ends the run when the model answers without a tool call; max_steps becomes isStepCount(n).
import { ToolLoopAgent, isLoopFinished, isStepCount } from 'ai'
import { createVertex } from '@ai-sdk/google-vertex'
import { tools } from './tools.ts'

const vertex = createVertex({
  project: process.env.GOOGLE_VERTEX_PROJECT,
  location: process.env.GOOGLE_VERTEX_LOCATION ?? 'global',
})

export const MODEL = process.env.LUNA_MODEL ?? 'gemini-3.8-flash'

export type LunaRequest = {
  system: string
  run: string
  question: string
  maxSteps?: number
  thinking?: 'minimal' | 'low' | 'medium' | 'high'
}

export function lunaAgent(r: LunaRequest) {
  const context = { run: r.run, question: r.question }
  return new ToolLoopAgent({
    model: vertex(MODEL),
    instructions: r.system,
    tools,
    toolsContext: { search_guidelines: context, read_page: context, list_guidelines: context },
    toolCallConcurrency: 4,
    stopWhen: r.maxSteps ? isStepCount(r.maxSteps) : isLoopFinished(),
    providerOptions: { vertex: { thinkingConfig: { thinkingLevel: r.thinking ?? 'low' } } },
  })
}
