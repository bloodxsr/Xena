# Nexued

This repository now runs on a JavaScript/TypeScript stack only.

## Services

- bot_js: Fluxer.js moderation bot runtime.
- web_dashboard_ts: Next.js staff dashboard for live moderation and verification workflows.

Recommended production data store for dashboard is PostgreSQL.

Local fallback uses shared SQLite at:

- bot_js/data/warnings.db

## Repository Layout

```text
fluxer_bot/
  README.md
  .gitignore
  bot_js/
    README.md
    .env.example
    package.json
    src/
    data/
  web_dashboard_ts/
    README.md
    .env.example
    package.json
    src/
```

## Quick Start

### 1. Run bot

```powershell
cd bot_js
npm install
copy .env.example .env
npm run start
```

Set FLUXER_BOT_TOKEN in bot_js/.env (or place token in bot_js/token.txt).

### 2. Run dashboard

```powershell
cd web_dashboard_ts
npm install
copy .env.example .env
npm run dev
```

Open http://127.0.0.1:3000/login

## Important Defaults

- Bot DB path: bot_js/data/warnings.db
- Bot word list: bot_js/data/words.json
- Dashboard DB default driver: postgres
- Dashboard SQLite fallback path: ../bot_js/data/warnings.db

## Notes

- The old Python bot code and requirements have been removed from this workspace.
- For SQLite fallback in the dashboard, use `BOT_DB_PATH=../bot_js/data/warnings.db`.
