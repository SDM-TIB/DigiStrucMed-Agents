// Guido v2 as a Mastra agent: the walk over the guideline knowledge graph, the guideline pages part of it. The
// instructions come with every request from the app (app/guido.py SYSTEM_V2); the model runs on Vertex AI, as Luna.
import { Agent } from '@mastra/core/agent'
import { createVertex } from '@ai-sdk/google-vertex'
import { expand, explore, readPageGuido, searchPages } from '../tools.ts'
import { MODEL } from './luna.ts'

const vertex = createVertex({
  project: process.env.GOOGLE_VERTEX_PROJECT,
  location: process.env.GOOGLE_VERTEX_LOCATION ?? 'global',
})

export const guido = new Agent({
  id: 'guido',
  name: 'Guido',
  description: 'Agentic graph RAG over the guideline knowledge graph with its pages, cited answers.',
  instructions: ({ requestContext }) => String(requestContext.get('system') ?? 'You are Guido.'),
  model: vertex(MODEL),
  tools: { explore, expand, search_pages: searchPages, read_page: readPageGuido },
})
