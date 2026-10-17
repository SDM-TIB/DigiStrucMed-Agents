# ui: the chat's AI Elements

The agent's work in TIB Chat's chat, rendered with [shadcn AI Elements](https://ui.shadcn.com/docs/ai-elements):

| Element | Where | Built from (shadcn registry) |
|---|---|---|
| Chain of Thought | above each answer: one collapsible summary ("Researched in 3 steps · 6 s"), open while Luna works, folded once she answers | `trace`, `badge` |
| Reasoning | a step inside it when Gemini returns thought summaries (`includeThoughts`, agent-server/) | `trace` |
| Tool | each `search_guidelines` / `read_page` / `view_page` / `list_guidelines` call: input (the query) to output (the pages found, with relevance and search mode); every page opens the source viewer | `trace`, `code-block`, `badge` |
| Sources | "Used n sources" under the answer, the cited pages | `trace`, `badge` |
| Inline Citation | hovering `[n]` in the answer: title, page, the matched passage and the rendered PDF page; a click or tap opens the source viewer as before | `hover-card`, `button` |

The rest of the chat stays in `app/static/js/app.js`: bubbles, the streamed text with its blur-in, history, the source
viewer, follow-up questions, the safety banner. app.js hands each assistant message to `window.LunaUI.message()`
(`src/luna-ui.tsx`) and feeds it the chat events; when the island has not loaded, app.js shows its plain step lines as
before. Guido keeps his graph timeline.

Isolation from the app's own CSS: Tailwind without its preflight, shadcn's tokens only inside `.lui` and on the hover
cards (mapped from the app's palette in `src/index.css`), and the app's page-wide `button` and `svg` defaults skip the
island (`app/static/css/app.css`).

## Build

```bash
npm ci
npm run build      # typecheck, then app/static/ui/luna-ui.js + luna-ui.css (git-ignored)
```

`python deploy/deploy.py app` builds it before the source deploy, and the GitHub workflow builds it in the check job
and before deploying. Node 22 or newer.

## Components

Installed with the shadcn CLI (`components.json`: Base UI, style base-nova, lucide icons):

```bash
npx shadcn@latest init -t vite -b base -p nova
npx shadcn@latest add trace code-block badge hover-card button
```

`src/components/ui/` is the registry code as installed; the compositions are in `src/elements/`, following the AI
Elements pages (Chain of Thought, Reasoning, Tool, Task, Sources, Inline Citation).
