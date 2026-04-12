# Workspace

> **⚠️ NEVER RESTART BOT — The bot runs on Railway. Restarting it here or on a new IP will sign out all connected Telegram accounts.**

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## Structure

```text
artifacts-monorepo/
├── artifacts/              # Deployable applications
│   └── api-server/         # Express API server
├── lib/                    # Shared libraries
│   ├── api-spec/           # OpenAPI spec + Orval codegen config
│   ├── api-client-react/   # Generated React Query hooks
│   ├── api-zod/            # Generated Zod schemas from OpenAPI
│   └── db/                 # Drizzle ORM schema + DB connection
├── scripts/                # Utility scripts (single workspace package)
│   └── src/                # Individual .ts scripts, run via `pnpm --filter @workspace/scripts run <script>`
├── pnpm-workspace.yaml     # pnpm workspace (artifacts/*, lib/*, lib/integrations/*, scripts)
├── tsconfig.base.json      # Shared TS options (composite, bundler resolution, es2022)
├── tsconfig.json           # Root TS project references
└── package.json            # Root package with hoisted devDeps
```

## TypeScript & Composite Projects

Every package extends `tsconfig.base.json` which sets `composite: true`. The root `tsconfig.json` lists all packages as project references. This means:

- **Always typecheck from the root** — run `pnpm run typecheck` (which runs `tsc --build --emitDeclarationOnly`). This builds the full dependency graph so that cross-package imports resolve correctly. Running `tsc` inside a single package will fail if its dependencies haven't been built yet.
- **`emitDeclarationOnly`** — we only emit `.d.ts` files during typecheck; actual JS bundling is handled by esbuild/tsx/vite...etc, not `tsc`.
- **Project references** — when package A depends on package B, A's `tsconfig.json` must list B in its `references` array. `tsc --build` uses this to determine build order and skip up-to-date packages.

## Root Scripts

- `pnpm run build` — runs `typecheck` first, then recursively runs `build` in all packages that define it
- `pnpm run typecheck` — runs `tsc --build --emitDeclarationOnly` using project references

## Packages

### `artifacts/api-server` (`@workspace/api-server`)

Express 5 API server. Routes live in `src/routes/` and use `@workspace/api-zod` for request and response validation and `@workspace/db` for persistence.

- Entry: `src/index.ts` — reads `PORT`, starts Express
- App setup: `src/app.ts` — mounts CORS, JSON/urlencoded parsing, routes at `/api`
- Routes: `src/routes/index.ts` mounts sub-routers; `src/routes/health.ts` exposes `GET /health` (full path: `/api/health`)
- Depends on: `@workspace/db`, `@workspace/api-zod`
- `pnpm --filter @workspace/api-server run dev` — run the dev server
- `pnpm --filter @workspace/api-server run build` — production esbuild bundle (`dist/index.cjs`)
- Build bundles an allowlist of deps (express, cors, pg, drizzle-orm, zod, etc.) and externalizes the rest

### `lib/db` (`@workspace/db`)

Database layer using Drizzle ORM with PostgreSQL. Exports a Drizzle client instance and schema models.

- `src/index.ts` — creates a `Pool` + Drizzle instance, exports schema
- `src/schema/index.ts` — barrel re-export of all models
- `src/schema/<modelname>.ts` — table definitions with `drizzle-zod` insert schemas (no models definitions exist right now)
- `drizzle.config.ts` — Drizzle Kit config (requires `DATABASE_URL`, automatically provided by Replit)
- Exports: `.` (pool, db, schema), `./schema` (schema only)

Production migrations are handled by Replit when publishing. In development, we just use `pnpm --filter @workspace/db run push`, and we fallback to `pnpm --filter @workspace/db run push-force`.

### `lib/api-spec` (`@workspace/api-spec`)

Owns the OpenAPI 3.1 spec (`openapi.yaml`) and the Orval config (`orval.config.ts`). Running codegen produces output into two sibling packages:

1. `lib/api-client-react/src/generated/` — React Query hooks + fetch client
2. `lib/api-zod/src/generated/` — Zod schemas

Run codegen: `pnpm --filter @workspace/api-spec run codegen`

### `lib/api-zod` (`@workspace/api-zod`)

Generated Zod schemas from the OpenAPI spec (e.g. `HealthCheckResponse`). Used by `api-server` for response validation.

### `lib/api-client-react` (`@workspace/api-client-react`)

Generated React Query hooks and fetch client from the OpenAPI spec (e.g. `useHealthCheck`, `healthCheck`).

### `scripts` (`@workspace/scripts`)

Utility scripts package. Each script is a `.ts` file in `src/` with a corresponding npm script in `package.json`. Run scripts via `pnpm --filter @workspace/scripts run <script>`. Scripts can import any workspace package (e.g., `@workspace/db`) by adding it as a dependency in `scripts/package.json`.

## Telegram Bot (`bot/`)

A standalone Python-based Telegram bot for intelligent medical link harvesting, filtering, and archiving.

### Stack
- **Language**: Python 3.x
- **Telegram library**: Telethon (MTProto)
- **Config**: python-dotenv
- **Data storage**: JSON files

### Bot Files
- `bot/main.py` — Bot entry point, control panel with inline buttons
- `bot/config.py` — API keys, keywords, channel names
- `bot/classifier.py` — Hierarchical keyword classification engine
- `bot/harvester.py` — Scrapes links from source groups
- `bot/sorter.py` — Deep-inspects and sorts links into archive channels
- `bot/account_manager.py` — Multi-account session management
- `bot/channel_setup.py` — Auto-creates Telegram archive channels
- `bot/database.py` — JSON data persistence, deduplication memory
- `bot/requirements.txt` — Python dependencies

### Documentation Files
- `bot/HOW_THE_BOT_WORKS.md` — Complete Arabic guide on how the bot works
- `bot/WHATS_MISSING.md` — What is not yet implemented / needs user input
- `bot/README.md` — Setup guide

### Running the Bot
```bash
cd bot && bash keep_alive.sh   # production-style (auto-restart on crash)
# or
cd bot && python -u main.py    # direct run (also has reconnect loop)
```

### Recent Fixes (March 2026)
- Sorting/publishing workflow update: full sorting and inline sorting now save categorized Telegram links into local `bot/sorted/*.txt` files first. Archive channels are updated only when the owner presses “📤 نشر إلى القنوات”, which publishes batched numbered messages.
- WhatsApp link export: WhatsApp links are stored separately in `bot/whatsapp_links.txt` and can be downloaded from the bot with “تنزيل ملف روابط الواتساب”.
- **Persistent progress bar**: Sorting now sends ONE message that gets edited each batch, showing a visual `▓▓▓▓░░` bar with %, counts, and Stop/Pause buttons. No more message spam.
- **Stop/Pause/Resume** for both sorting AND harvesting.
- **Sorting logic fix**: Invite links (`t.me/+...`) that can't be accessed are now correctly routed to the "دعوات" archive (not falsely counted as "broken"). "Broken" now means truly deleted/invalid usernames only.
- **Group extraction fix**: Added `flood_sleep_threshold=60` so Telethon auto-retries short FloodWaits during `iter_messages`, preventing mid-group extraction stops.
- **Periodic saves**: Harvest saves every 500 newly found links (not just at the end) so partial progress is never lost.
- **keep_alive.sh**: Development workflow now also uses keep_alive.sh with `-u` flag for unbuffered logs.
