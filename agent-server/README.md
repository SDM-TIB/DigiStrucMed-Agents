# Agent server (Luna)

Luna's agent loop runs on [Mastra](https://mastra.ai), which builds on the [Vercel AI SDK](https://ai-sdk.dev). It is
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
| `Dockerfile` | `mastra build` in Node 24, then the self-contained `.mastra/output` server |

## Loop control

These are the AI SDK loop controls, used through Mastra:
- **`maxSteps = rounds + 1`.** The default is 6 tool steps. Several tool calls in one step run concurrently (`toolCallConcurrency` 4).
- **`prepareStep` on the answer step.** It removes the tools (`activeTools: []`, `toolChoice: 'none'`) and adds a system message that the last tool step has passed, so every run ends in an answer.
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
