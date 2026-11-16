# Repository variables (settings, not secrets)

GitHub > Settings > Secrets and variables > Actions > Variables. The deploy job passes them to `deploy/deploy.py`.
Values since 30 October 2026, after the move to the institute's Google Cloud project (repository variables set on
30 October 2026, 22:00 UTC).

| Variable | Used by | Meaning |
|---|---|---|
| `GCP_PROJECT` | all | Google Cloud project, `project-14b5b564-b173-4118-937` |
| `GCP_REGION` | all | `europe-west3` |
| `CLOUD_RUN_SERVICE` | all | the app's service name, `luna-demo` |
| `WIF_PROVIDER` | sign-in | Workload Identity provider of the project (pool `github`, provider `digistrucmed-agents`): `projects/601538198682/locations/global/workloadIdentityPools/github/providers/digistrucmed-agents` |
| `DEPLOY_SERVICE_ACCOUNT` | sign-in | `github-deployer@project-14b5b564-b173-4118-937.iam.gserviceaccount.com` |
| `PAGE_BUCKET` | app | bucket with page images and the search index, `luna-pages-tib-chat-nb` |
| `LLM_BASE_URL` | app | Vertex AI OpenAI-compatible endpoint of the project: `https://aiplatform.googleapis.com/v1/projects/project-14b5b564-b173-4118-937/locations/global/endpoints/openapi` |
| `LLM_MODEL`, `LLM_FALLBACK_MODEL`, `LLM_REASONING_EFFORT` | app | answer model, fallback, reasoning effort |
| `LANGUAGES` | app | UI languages, `en,de,es,ru,hi` |
| `MAX_CALL_SECONDS` | app | maximum length of a video call |
| `GRAPH_ENDPOINT` | app | internal SPARQL address of the graph server, `http://10.156.0.2:7001` |
| `EMBED_URL` | app | the embedding service |
| `AGENT_URL`, `AGENT_AISDK_URL` | app | the two agent servers |
| `GUIDO_CALL_WRITER` | app | Guido's video call on the walker: `1` (default) the walker's writer answers and the video agent rephrases it; `0` the video agent answers from the selected statements itself (faster, not evaluated) |
| `V5_SCORER`, `V7_RERANK` | app | the Graph Walker's decision models for the first pass and the strict second pass. Empty = `@cf/cloudflare/clef-flash` and `@cf/cloudflare/clef`. A name without `@cf/` is called on OpenRouter (`google/gemini-decision-1`, `liquid/d1-ultra`) and needs the secret `openrouter-api-key` |
| `MODEL_SLOTS` | app | model calls of one app instance at the same time for the walker (default 8) |
| `GUIDO_HARNESS` | app | `walker` (Walker V7 on the proposition graph, needs the walker data pack in the page bucket; falls back to `mastra`), `mastra` (Guido v2) or `local` |
| `LUNA_IMAGE_TOOL` | app | `1` gives Luna the page-image tool (measured, off) |
| `CLAIM_GUARD` | app | `1` checks every uncited statement of Luna's answers with clef-flash (`app/claimguard.py`) |
| `LUNA_MODEL` | agent servers | Gemini model of the agent loop; empty means `gemini-3.8-flash` |
| `TAVUS_PERIOD_START`, `TAVUS_PLAN_MINUTES`, `TAVUS_USED_OFFSET_MIN` | app | video-call minute budget display |

After changing a variable, redeploy the component that uses it (Run workflow > `app`, or `agents`).

Not variables: the secrets `TAVUS_API_KEY`, `TAVUS_EXTRA_KEYS`, `DEMO_PASSWORD`, `ADMIN_PASSWORD`, `LLM_API_KEY`,
`LINO_API_KEY`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `MODAL_EXTRA_TOKENS`, `CF_ACCOUNT_ID`, `CF_API_TOKEN` live in
Google Secret Manager (secret names in lower case with dashes, e.g. `demo-password`).
