# TIB Chat

Three ways to answer a heart failure question from clinical guidelines, side by side, in chat and in video calls.

| Agent | How it answers | Runs on |
| --- | --- | --- |
| **Luna** | Searches 2,780 guideline pages (BM25) and cites the pages she read. | Any OpenAI compatible LLM. Gemini 3.5 Flash-Lite by default |
| **Lino** | Answers from memory. Llama 3.2 1B, fine-tuned with NeSyCoT rules mined from the guideline knowledge graph. No retrieval, no guardrails. | vLLM on a Modal L4 that sleeps when unused |
| **Guido** | Walks the guideline knowledge graph (Think-on-Graph), shows every hop live and cites each recommendation with class and level of evidence. | Same LLM as Luna |

All three speak English, German, Spanish, Russian and Hindi. They answer in the language you use and switch when you switch, in chat and in calls (`LANGUAGES`).

Every agent can also take a video call through Tavus. When Luna talks about the guidelines in a call, the right side shows what she is drawing on: verbatim quotes from the guideline pages, checked character by character against the source, with guideline, page, class and level of evidence, and the original table with the row highlighted. Small talk shows nothing. In Lino's call the avatar only speaks: every question goes to the fine-tuned model on Modal through the `ask_lino` tool, and its answer is read out unchanged, so the call needs his GPU awake. Guido shares a fake screen, and a small decision model (Cloudflare `clef-flash`) arranges the windows: the graph walk, the original guideline table, live UMLS and Wikidata facts, and notes of what he said.

This is a research demo. It is not clinical decision support and nothing it says replaces medical advice.

## How it fits together

```
browser ─── Cloud Run (app/, one python process, stdlib http server)
              ├── llm.py      ─── any /chat/completions api      Luna, Guido, live boards
              ├── lino.py     ─── vLLM on Modal (gpu/serve.py)   Lino
              ├── gpu.py          wakes and parks the Modal gpu
              ├── tavus.py    ─── Tavus                          video calls
              ├── director.py ─── Cloudflare Workers AI          guido's screen layout
              ├── wikidata.py ─── query.wikidata.org             live concept facts
              └── storage.py  ─── private GCS bucket             page renders, usage log
```

The server keeps state in memory (gpu timer, call timers, usage log), so it runs as exactly one instance.

## Repository

```
app/            the Cloud Run service. deployed as is
  server.py     routes. start here
  data/         corpus, knowledge graph, concept links, Tavus ids (all built by pipeline/)
  static/       index.html, dashboard.html, css, js, images. videos are in the bucket
gpu/            Lino on Modal. serve.py serves, bench.py measures tokens per second
tavus/          the three video agents and their tools as json. sync.py pushes them to Tavus
pipeline/       rebuilds app/data from the MHH export and guidelineKG.nt
deploy/         deploy.py: Cloud Run, Modal, and the large files in the bucket
```

## Run it locally

You need Python 3.12 and a filled `.env` (copy `.env.example`).

```bash
python deploy/deploy.py assets pull
```

```bash
cd app && python server.py
```

Then open http://localhost:8080. Without `PAGE_BUCKET` the server reads page renders from `page_images/` next to `app/`. Build them with `pipeline/build_page_images.py` if you want the page view locally.

## Deploy from scratch

1. Create a Google Cloud project, a private bucket and log in with `gcloud auth login`. Put the ids in `.env`.
2. Upload the large files: `python deploy/deploy.py assets push`. They come from whoever has a working copy.
3. Deploy Lino: `pip install modal`, `modal token new`, then `python deploy/deploy.py gpu`. Copy the printed URL into `LINO_URL`.
4. Create the video agents: `python tavus/sync.py`. Pick faces in the Tavus dashboard first and put their ids into `tavus/pals/*.json` as `default_face_id`. Luna's `document_ids` only exist in our Tavus account, so remove them on a new one.
5. Deploy the app: `python deploy/deploy.py app`. It stores every secret in Secret Manager and prints the URL.

Cloudflare is optional. Without `CF_ACCOUNT_ID` the screen share keeps its default layout.

## Change the LLM

Set `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` to any API that speaks OpenAI Chat Completions with streaming and tool calls: OpenAI, OpenRouter, a vLLM or Ollama server. Leave `LLM_REASONING_EFFORT` empty if the API doesn't know that parameter. We recommend Gemini 3.5 Flash-Lite: it is fast, cheap and reliable with tools. When the main model runs out of quota, requests move to `LLM_FALLBACK_MODEL` (default `gemini-3.1-flash-lite`, which has its own rate limit). Set it empty to turn that off.

## Rebuild the data

```bash
python pipeline/build_corpus.py <clean_ready_07_10>/pages <guidelineKG.nt>
```

```bash
python pipeline/build_kg_graph.py <guidelineKG.nt>
```

```bash
python pipeline/build_kg_links.py <clean_ready_07_10>
```

Both graph builders reproduce the committed files exactly (checked on 2026-10-08).

## How Guido searches

Think-on-Graph (Sun et al., ICLR 2024) walks from the concepts in the question. Its weak spot is the start: when no concept label matches, the walk finds nothing even though the answer sits in a recommendation's text. So Guido starts like HippoRAG (Gutiérrez et al., NeurIPS 2024). `app/retrieve.py` ranks concept labels and the recommendation texts (BM25) against the query, fuses the rankings with reciprocal rank fusion, and runs personalized PageRank from those seeds over the graph. The recommendations with the most PageRank mass are the evidence closest to the question, even when no label matched. Searching the passages directly is the HippoRAG 2 lesson (ICML 2025). Guido can then expand any node or search again with other words.

If the language model fails after the walk has found evidence, Guido still answers with the top recommendations he found instead of an error.

Dense vectors are optional. `python pipeline/build_kg_vectors.py` embeds every node into `app/data/kg_vectors.npz`, and the same fusion uses them. The free Gemini tier allows only 1000 embeddings a day, so this needs a paid key, and every question then costs one embedding call. The demo runs without them.

## Things to know

- **Costs.** Tavus bills call minutes, so every call ends after `MAX_CALL_SECONDS`. The Modal gpu costs money while awake. The start screen asks how long to keep it up, and Settings has a button that shuts it down. Only Lino needs the gpu. Luna and Guido run on the LLM API and are always there.
- **Several visitors at once.** There are no accounts. The team password gives each browser its own random session id in a signed cookie. Each session holds its own lease on the gpu, so the gpu stays up while anyone still uses Lino, and Shut down only drops your own lease unless you are signed in as admin. A call can only be ended by the session that started it.
- **Tavus minutes.** Tavus has no balance API. The app sums call durations since `TAVUS_PERIOD_START` and applies `TAVUS_USED_OFFSET_MIN`, calibrated by hand against the Tavus dashboard.
- **About us.** The team on the About page comes from `app/static/team.json`. Put photos in `app/static/img/team/` and their file names into the json.
- **Usage log.** `/dashboard` shows logins, chats, calls and gpu actions with ip, browser and session. It has its own `ADMIN_PASSWORD`. IPs and questions are personal data, so tell your visitors.
- **Link quality.** UMLS links from the MHH export are rank-1 retrieval candidates, not confirmed links, and the UI marks them as candidates. The MHH Wikidata candidates are never used: spot checks found wrong items.
- **Lino makes things up.** He invents references and is often wrong. That's the point of the comparison, so don't add guardrails to him.

## Credits

Lino's model and the NeSyCoT fine-tune are Mahsa's work. The guideline knowledge graph comes from the DigiStrucMed team at TIB. The video storyboard prompt is Mahsa's best prompt, unchanged. Tool calling against Tavus follows their docs at docs.tavus.io. `gpu/serve.py` follows modal-labs/modal-examples.
