# Embedding server

EmbeddingGemma 2 (`google/embeddinggemma-2`, revision `914f7f89142e33e77833254d9c9b90c3cef7303b`, Apache 2.0) as a
small private Cloud Run service with an OpenAI-compatible endpoint. It is the query encoder of Luna's search
(`app/search.py`).

```
Luna ── search_guidelines(query) ──> app/search.py
           1. POST tib-embed /v1/embeddings  (question-answering and search-result prompt)
           2. 8 dense rankings over the page index (search_index.npz) + BM25, reciprocal rank fusion -> top 40 pages
           3. clef-flash (Cloudflare Workers AI) judges each of the 40 pages -> order and relevance for Luna
```

| file | what |
|---|---|
| `Dockerfile` | Python 3.10, the CPU build of torch 2.6.0, sentence-transformers 6.1.0 and transformers 5.20.1 (the versions of the evaluated environment), the model baked in at its pinned revision |
| `server.py` | `POST /v1/embeddings` (`{"input": [...]}`, at most 64 texts) and `GET /health`; texts are embedded as given, the caller adds the prompts |

## Why these settings

The retrieval study in the thesis repository chose the design and measured it:
- the study: MASTERTHESIS-HEART, `projects/03_reasoning_aware_guideline_retrieval/experiments/2026-10-17_embeddinggemma2_retrieval/README.md`;
- the settings: chunks of 400 characters with the guideline title, the question-answering query prompt, and clef-flash reranking of 40 candidates;
- the result on a held-out test half: R@5 0.971 against 0.689 for BM25.

The integration check (`integration_eval.py` there) ran this code against the deployed service and the published
index. On the same test half it found R@5 0.963 and R@1 0.797. The vectors of this service and of the GPU run that
built the index agree to a cosine of at least 0.99993.

## Deploy

```
python deploy/deploy.py embed      # builds and deploys tib-embed, lets the app's runtime account invoke it, prints EMBED_URL
```

Then set `EMBED_URL` in `.env` and as the repository variable `EMBED_URL`, and deploy the app.

- The service is private (`--no-allow-unauthenticated`); the app calls it with its identity token.
- It scales to zero, with 2 vCPU and 4 GiB. The app wakes it when the app starts.
- While it is cold, the search uses BM25 and says so (`mode` in the sources event, `/api/search/status`).
- One search embeds two texts in about 1.7 s from a warm service.

## The index

`pipeline/build_search_index.py` packs the page vectors into `search_index.npz`:
- views `chunk_title_s400`, `chunk_title_s600`, `chunk_title` and `page_plain`, together 94,628 vectors;
- uploaded to `gs://<PAGE_BUCKET>/search/<sha12>/`;
- pinned in `app/search.lock.json`.

The vectors were computed on a GPU (KISSKI) with the same model and versions. The builder refuses vectors whose units
differ from `app/pageunits.py` on the current page corpus. A changed corpus needs a new index: run the builder with
`--embed-url` against this service, or rerun `embed.py` of the study on a GPU.
