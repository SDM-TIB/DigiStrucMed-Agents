# Repository variables (settings, not secrets)

GitHub > Settings > Secrets and variables > Actions > Variables. The deploy job passes them to `deploy/deploy.py`.
Values as of 19 October 2026.

| Variable | Used by | Meaning |
|---|---|---|
| `GCP_PROJECT` | all | Google Cloud project, `tib-chat-20261019` |
| `GCP_REGION` | all | `europe-west3` |
| `CLOUD_RUN_SERVICE` | all | the app's service name, `luna-demo` |
| `WIF_PROVIDER` | sign-in | Workload Identity provider of the project (pool `github`, provider `digistrucmed-agents`) |
| `DEPLOY_SERVICE_ACCOUNT` | sign-in | `github-deployer@tib-chat-20261019.iam.gserviceaccount.com` |
| `PAGE_BUCKET` | app | bucket with page images and the search index, `luna-pages-tib-chat-20261019` |
| `LLM_BASE_URL` | app | Vertex AI OpenAI-compatible endpoint of the project |
| `LLM_MODEL`, `LLM_FALLBACK_MODEL`, `LLM_REASONING_EFFORT` | app | answer model, fallback, reasoning effort |
| `LANGUAGES` | app | UI languages, `en,de,es,ru,hi` |
| `MAX_CALL_SECONDS` | app | maximum length of a video call |
| `GRAPH_ENDPOINT` | app | internal SPARQL address of the graph server, `http://10.156.0.2:7001` |
| `EMBED_URL` | app | the embedding service |
| `AGENT_URL`, `AGENT_AISDK_URL` | app | the two agent servers |
| `GUIDO_HARNESS` | app | `mastra` (Guido v2) or `local` |
| `LUNA_IMAGE_TOOL` | app | `1` gives Luna the page-image tool (measured, off) |
| `LUNA_MODEL` | agent servers | Gemini model of the agent loop; empty means `gemini-3.8-flash` |
| `TAVUS_PERIOD_START`, `TAVUS_PLAN_MINUTES`, `TAVUS_USED_OFFSET_MIN` | app | video-call minute budget display |

After changing a variable, redeploy the component that uses it (Run workflow > `app`, or `agents`).

Not variables: the secrets `TAVUS_API_KEY`, `TAVUS_EXTRA_KEYS`, `DEMO_PASSWORD`, `ADMIN_PASSWORD`, `LLM_API_KEY`,
`LINO_API_KEY`, `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `MODAL_EXTRA_TOKENS`, `CF_ACCOUNT_ID`, `CF_API_TOKEN` live in
Google Secret Manager (secret names in lower case with dashes, e.g. `demo-password`).
