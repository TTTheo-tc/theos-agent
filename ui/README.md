# TheOS Knowledge UI

React single-page workspace for personal memory, learning notes, scheduled work, and plans.

## Current Scope

Only four frontend pages are in scope:

| Path | Page | Purpose |
|---|---|---|
| `/memory` | Memory | Instinct framework, recall signals, knowledge graph nodes, and memory search |
| `/wiki` | Wiki | Local LLM Wiki for personal learning notes and source-backed Markdown pages |
| `/cron` | Cron | Scheduled jobs and recurring prompts |
| `/plans` | Plans | Daily focus and long-term plans |

`/` redirects to `/memory`. Unknown routes also redirect to `/memory`.

Removed from the frontend scope: dashboard overview, timeline, cost analytics, channel status, logs, config editor, tool registry, and settings.

## Stack

- React 19
- React Router 7
- Vite 6
- TypeScript 5
- Tailwind CSS 4
- shadcn/Radix UI components
- Lucide icons
- cmdk command palette

## Development

Install dependencies first:

```bash
npm install
```

Run the Vite dev server:

```bash
npm run dev
```

The dev server uses Vite's default port from `vite.config.ts`:

```text
http://localhost:5173
```

During development, `/api/*` requests are proxied to the Python dashboard server at:

```text
http://localhost:8080
```

Start the backend dashboard API with the gateway or standalone UI command before using live data.

The active pages refresh live data with after-delay sync: each request finishes first, then the next refresh is scheduled after the delay. Window focus also schedules a short delayed refresh.

- Memory instinct summary: 10 seconds after the previous sync finishes
- Memory graph nodes: 15 seconds after the previous sync finishes
- Cron jobs: 10 seconds after the previous sync finishes
- Wiki status and file index: 15 seconds after the previous sync finishes
- Plans: browser-local state, synced across tabs with the `storage` event

## Wiki Workspace

The Wiki page is separate from Memory. It uses a local Markdown workspace under:

```text
<workspace>/llm-wiki/
  raw/              # original source material, read-only by convention
  wiki/
    index.md        # page directory
    log.md          # operation log
    concepts/       # concept pages
    entities/       # people, projects, companies, tools
    sources/        # one summary page per raw source
    outputs/        # reusable synthesis and answers
  CLAUDE.md         # schema and workflow rules for LLM agents
```

The UI can initialize this structure, browse Markdown files, read a selected page, search text files, and record new `sources`, `concepts`, `entities`, or `outputs` pages. Recording writes a Markdown page, appends one line to `wiki/index.md`, and appends an operation entry to `wiki/log.md`. Full LLM-backed ingest, query, and lint flows are still future work.

## Scripts

```bash
npm run dev      # start Vite dev server
npm run build    # type-check and build static assets
npm run preview  # preview the production build
```

## Source Layout

```text
src/
  App.tsx                    # four-page React Router table
  main.tsx                   # React root entry
  app/globals.css            # Tailwind v4 and theme variables
  pages/
    Memory.tsx               # instinct, memory nodes, and search
    Wiki.tsx                 # local LLM Wiki browser
    Cron.tsx                 # cron job list/actions
    Plans.tsx                # local daily/long-term plans
  components/layout/         # sidebar, header, command palette
  components/ui/             # shadcn/Radix primitives
  lib/utils.ts               # className merge helper
```

## Backend Contract

Relevant endpoints currently used by the UI:

| Endpoint | Methods | Used by |
|---|---|---|
| `/api/memory/nodes` | GET | Memory node list |
| `/api/memory/search` | GET | Memory search |
| `/api/memory/instinct` | GET | Instinct framework and runtime summary |
| `/api/wiki/status` | GET | Wiki workspace status and file index |
| `/api/wiki/init` | POST | Create the local Wiki directory structure |
| `/api/wiki/page` | GET | Read a selected Wiki file |
| `/api/wiki/record` | POST | Create a Wiki Markdown page and update index/log |
| `/api/wiki/search` | GET | Search Wiki text files |
| `/api/cron/jobs` | GET | Cron job list |
| `/api/cron/jobs/{job_id}` | PUT, DELETE | Cron enable/disable and delete |
| `/api/cron/jobs/{job_id}/run` | POST | Cron manual run |

Plans currently persist in `localStorage` under `theos.ui.plans`. Add a backend API before treating plans as shared or durable across browsers.

## Production Build

Build static assets with:

```bash
npm run build
```

The Python UI server serves `ui/dist/` when it exists, or packaged static files from `src/ui/ui_static/`.
