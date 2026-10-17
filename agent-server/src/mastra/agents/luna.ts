// Luna as a Mastra agent. The instructions come with every request from the TIB Chat app (app/luna.py SYSTEM), so
// the prompt, its language rule and the step budget have one source; the model runs on Vertex AI.
import { Agent } from '@mastra/core/agent'
import { createVertex } from '@ai-sdk/google-vertex'
import { listGuidelines, readPage, searchGuidelines, viewPage } from '../tools.ts'

const vertex = createVertex({
  project: process.env.GOOGLE_VERTEX_PROJECT,
  location: process.env.GOOGLE_VERTEX_LOCATION ?? 'global',
})

export const MODEL = process.env.LUNA_MODEL ?? 'gemini-3.8-flash'

export const luna = new Agent({
  id: 'luna',
  name: 'Luna',
  description: 'Agentic RAG over cardiology guideline pages with cited answers.',
  instructions: ({ requestContext }) => String(requestContext.get('system') ?? 'You are Luna.'),
  model: vertex(MODEL),
  // view_page only when the request asks for it (the app's image_tool switch)
  tools: ({ requestContext }) => ({
    search_guidelines: searchGuidelines,
    read_page: readPage,
    list_guidelines: listGuidelines,
    ...(requestContext.get('imageTool') === true ? { view_page: viewPage } : {}),
  }),
})
