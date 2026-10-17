# Agent server, AI SDK 7 (Luna, second harness)

Luna's agent loop as an [AI SDK 7](https://ai-sdk.dev) `ToolLoopAgent`. The model is Gemini 3.8 Flash on Vertex AI
(`@ai-sdk/google-vertex`). It is a private Cloud Run service (`luna-agent-aisdk`).

It is the second harness next to `agent-server/` (Mastra, also built on the AI SDK) and has the same contract:
- `POST /luna/chat`, which streams TIB Chat's chat events;
- the same three tools, calling the app's evaluated retrieval through `/api/agent/tools/*`;
- the same prompt, sent by the app.

The app uses Mastra by default. A request with `"harness": "aisdk"` goes here, which is how the two are compared on the QA test set.

| file | what |
|---|---|
| `src/agent.ts` | `lunaAgent()`: a `ToolLoopAgent` per request. Instructions come from the app. The tools' typed context (`toolsContext`: run id, the user's question) belongs to the request. `stopWhen` is `isLoopFinished()` (no step limit) or `isStepCount(n)` when an evaluation passes `max_steps`. Thinking level `low` |
| `src/tools.ts` | three `tool()` definitions with zod `inputSchema` and `contextSchema`. `toModelOutput` gives the model only the `model` part of the app's answer |
| `src/server.ts` | a `node:http` server: the route, `GET /health`, and the AI SDK `fullStream` mapped to the app's events |
| `Dockerfile` | Node 24 runs the TypeScript sources directly (type stripping), with no build step |

Versions are pinned: `ai` 7.0.143, `@ai-sdk/google-vertex` 5.0.115 (both on `@ai-sdk/provider` 4.0.29), `zod` 4.6.7.

Why not the AI SDK "harnesses" (`HarnessAgent`): those run complete coding-agent runtimes (Claude Code, Codex) in a
sandbox and are experimental. The AI SDK docs recommend a model with a tool loop for a custom agent architecture,
which is this one.

## Deploy

```
python deploy/deploy.py agent-aisdk     # deploys luna-agent-aisdk, lets the app's account invoke it, prints AGENT_AISDK_URL
```

Set `AGENT_AISDK_URL` as the repository variable and in `.env`, then deploy the app.
