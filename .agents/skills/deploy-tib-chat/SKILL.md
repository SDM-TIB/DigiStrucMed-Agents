---
name: deploy-tib-chat
description: How TIB Chat is built and deployed (GitHub Actions to Google Cloud Run, no keys), which component a change deploys, how to deploy one component or all of them, and how to check the result. Use it when someone wants to deploy, asks how the CI/CD works, changed the app, an agent server or the embedding service, or a deploy failed.
license: MIT
metadata:
  project: tib-chat-20261019
  updated: "2026-10-19"
---

# Deploying TIB Chat

TIB Chat runs on Google Cloud Run in project `tib-chat-20261019` (region `europe-west3`). Deploys go through
GitHub Actions (`.github/workflows/deploy.yml`). A team member needs **write access to this GitHub repository and
nothing else**: no Google Cloud account, no keys, no `.env`.

## The components

| Component | Folder | Cloud Run service | What it does |
|---|---|---|---|
| App | `app/`, `ui/` (chat UI island), `deploy/` | `luna-demo` | web app, search, tool endpoints, Luna/Lino/Guido chat |
| Agent server | `agent-server/` | `luna-agent` | Luna's and Guido's agent loop (Mastra, Gemini on Vertex AI) |
| Second harness | `agent-server-aisdk/` | `luna-agent-aisdk` | the same agent on AI SDK 7 (comparison) |
| Embedding service | `embed-server/` | `tib-embed` | EmbeddingGemma 2 for the page search |
| Graph server | `graph-server/` | VM `qlever-graph-server` | QLever SPARQL server for the knowledge graph (owner only) |

Live app: https://luna-demo-4kh6ub6nmq-ey.a.run.app (the former address redirects there).

## How a deploy happens

1. Every push and pull request runs the **check** job: Python lint (`pyflakes`), JavaScript syntax, and the chat UI
   build (`ui/`).
2. A push to `main` (or a merged pull request) runs the **deploy** job, which deploys only what the push changed:

   | Push changes | Deploys | Command the job runs |
   |---|---|---|
   | `app/`, `ui/`, `deploy/` | `luna-demo` | `python deploy/deploy.py app --code-only` |
   | `agent-server/` | `luna-agent` | `python deploy/deploy.py agent --code-only` |
   | `agent-server-aisdk/` | `luna-agent-aisdk` | `python deploy/deploy.py agent-aisdk --code-only` |
   | `embed-server/` | `tib-embed` | `python deploy/deploy.py embed --code-only` |

3. The job signs in to Google Cloud **without a key**: Workload Identity Federation (pool `github`, provider
   `digistrucmed-agents`, which accepts tokens of this repository only) lets it act as
   `github-deployer@tib-chat-20261019.iam.gserviceaccount.com`. That account may only deploy from source.
4. Cloud Build builds the image from the folder, Cloud Run starts a new revision and moves traffic to it once it is
   healthy. **A failed build or a revision that does not start changes nothing**: the old revision keeps serving.
5. Settings (URLs, model names, bucket) come from the repository variables (Settings > Secrets and variables >
   Actions > Variables). Secrets (passwords, Tavus, Modal, Cloudflare keys) stay in Google Secret Manager; the
   services keep their references. `--code-only` never touches either.

Measured on 19 October 2026 (manual run `all`): embedding service 8 min (the model is baked into the image), agent
server 2 min, second harness 1 min, app 2 min; the whole run 13 min.

## Deploy one component

**Normal way: push.** Change files in the component's folder, commit, push to `main`. Only that component deploys.

**Without a code change** (redeploy, e.g. after a variable changed): GitHub > Actions > "check and deploy" > Run
workflow > choose `app`, `agents` (both agent servers), `embed` or `all`. With the GitHub CLI:

```bash
gh workflow run deploy.yml -R SDM-TIB/DigiStrucMed-Agents -f what=app      # or agents | embed | all
gh run watch -R SDM-TIB/DigiStrucMed-Agents $(gh run list -R SDM-TIB/DigiStrucMed-Agents --limit 1 --json databaseId -q '.[0].databaseId')
```

Order when several parts change together: nothing to do, the job deploys the embedding service and the agent servers
before the app.

## Check the result

1. Actions tab: the run is green; the step "plan" prints what it deployed (`deploy: app=true agent=false ...`).
2. Open the live app, log in, ask Luna one question. The answer must show the search steps and sources.
3. Search page: switch on "Embeddings" and search once (checks `tib-embed` and the search index).
4. Guido: ask one question (checks the agent server and the graph server).

## Change a setting

Settings are repository variables, changed by anyone with admin rights on the repository (Settings > Secrets and
variables > Actions > Variables), then redeploy the app (Run workflow > `app`). The variables and what they do are
listed in [references/settings.md](references/settings.md).

## What a team member cannot do (and why)

- **Secrets** (passwords, API keys): only the project owner, from a local `.env` with `python deploy/deploy.py app`.
  Keys never go into GitHub.
- **Graph server** (`graph-server/`, terraform) and **Lino's GPU on Modal** (`python deploy/deploy.py gpu`): need
  owner credentials. See [references/owner-tasks.md](references/owner-tasks.md).

## When a deploy fails

| Symptom | Cause | Fix |
|---|---|---|
| check job red | lint error, JS syntax error or the UI does not build | read the failing step, fix, push again |
| deploy step "auth" fails | workflow or variables `WIF_PROVIDER` / `DEPLOY_SERVICE_ACCOUNT` changed | ask the project owner |
| build fails in Cloud Build | the folder does not build (Dockerfile, dependencies) | the log link in the step output shows the build log |
| revision does not start | the app crashed at startup (e.g. graph server unreachable) | old revision keeps serving; look at Cloud Run logs or ask the owner |
| answers take minutes right after a deploy, then normal again | Vertex AI briefly rejects calls (HTTP 429) and the agent retries; seen on 19 October 2026 for about 10 minutes | wait and ask again; if it lasts, tell the owner (quota) |
| app answers but search is slow the first time | the embedding service scaled to zero (cold start about 50 s) | normal; the app wakes it when opened |

Rules: never commit `.env`, keys or generated `app/static/ui/` files; never disable a check to get a deploy through.
