# Agent server (Luna, Guido v2)

The agent loops of Luna and of Guido v2 run on [Mastra](https://mastra.ai), which builds on the [Vercel AI SDK](https://ai-sdk.dev). It is
a private Cloud Run service (`luna-agent`). The model is Gemini 3.8 Flash on Vertex AI (`@ai-sdk/google-vertex`,
location `global`).

```
browser ── /api/chat/luna ──> app (Python, luna.run)
                                │  POST /luna/chat (identity token of the app's service account; Cloud Run IAM)
                                v
                         agent server (Mastra Agent, Gemini 3.8 Flash)
                                │  tools: search_guidelines, read_page, list_guidelines
                                v
                         app /api/agent/tools/*  (identity token of the same account, checked by the app)
                                └─> search.py: EmbeddingGemma 2 + clef-flash, luna.py: windows, pages, catalogue
```

| file | what |
|---|---|
| `src/mastra/index.ts` | the Mastra instance and the route `POST /luna/chat`, which streams the app's chat events (text, tool_call, sources, read, done, error) |
| `src/mastra/agents/luna.ts` | the `Agent`: instructions come with each request (one prompt source, `app/luna.py` `SYSTEM`), the model via `createVertex` |
| `src/mastra/tools.ts` | three `createTool()` tools with zod schemas. Each calls the app; `toModelOutput` gives the model only the `model` part, and the ui events go to the browser |
| `src/mastra/agents/guido.ts` | Guido v2: the same model and loop, instructions from `app/guido.py` `SYSTEM_V2`, route `POST /guido/chat` |
| `Dockerfile` | `mastra build` in Node 24, then the self-contained `.mastra/output` server |

## Loop control

These are the AI SDK loop controls, used through Mastra:
- **Mastra's loop, unmodified.** The model calls tools as it sees fit and stops when it has answered; several tool calls in one step run concurrently (`toolCallConcurrency` 4).
- **No step limit.** The run ends when the model answers without a tool call: `stopWhen: isLoopFinished()` from the AI SDK (`ai`). Without a stop condition Mastra would end after 5 steps. The prompt sets no step budget. An evaluation can pass `max_steps`, which becomes `stepCountIs(n)`. `ai` is pinned to 6.x, the AI SDK generation whose `StopCondition` Mastra 1.78 accepts.
- **Thinking level `low`** (`providerOptions.vertex.thinkingConfig`), as in the app's other agents.
- **Context.** `toModelOutput` keeps the browser's source cards out of the model's context. Page windows instead of whole pages keep search results short, and whole pages come only through `read_page`.

## Why retrieval stays in the app

The retrieval is the part the thesis study measured: EmbeddingGemma 2 index and clef-flash reranking, with R@5 0.963 for the deployed code on the held-out test half. Keeping it in one place means the agent framework changes the loop, not the evidence. The tools are thin HTTP clients.

## Deploy

```
python deploy/deploy.py agent      # builds and deploys luna-agent, lets the app's account invoke it, prints AGENT_URL
```

Then set `AGENT_URL` as the repository variable and in `.env`, and deploy the app. Without `AGENT_URL`, or when the
agent server cannot be reached before it answers, `luna.run` runs the same loop in-process (`_local`).

## Local development

Node ≥ 22.18 is required by Mastra.

```
APP_URL=http://localhost:8080 AGENT_TOKEN=<any> GOOGLE_VERTEX_PROJECT=<project> npx mastra dev
```

Then run the app with the same `AGENT_TOKEN` and `AGENT_URL=http://localhost:4111`.

## Guido v2

Guido walks the guideline knowledge graph (app/guido.py `Walk`); in v2 the guideline pages are part of that graph.

- **Page nodes.** `p:<page id>` for the 2,743 pages with linked mentions. A page names a concept when one of its detected
  mentions is linked to the concept's UMLS concept, both as the selected Top1 of their link sets (172,332 links, loaded
  at startup by `graph.agent_view`, query `pagecon`). A recommendation knows its page from its source block. Walk
  weights are tf-idf, so generic concepts ("patient") do not pull the walk to random pages.
- **Tools** (app endpoints `guido_*`, one walk per run in `app/agent_tools.py`): `explore` (concepts, the recommendations
  closest to them by personalized PageRank, and the pages where the concepts meet, ranked by how well the page text fits
  the question), `expand` (a concept, recommendation or page node; from a page to its concepts and recommendations),
  `search_pages` (Luna's two-stage page search; the pages join the graph) and `read_page` (the original OCR page).
- **Selection.** `GUIDO_HARNESS` (app setting): `local` is the evaluated 3-round loop in `guido.py`, `mastra` is v2.
  A request may choose with `harness`.
