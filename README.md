# Xena

This repository now runs on a JavaScript/TypeScript stack only.

## Services

- bot_js: Fluxer.js moderation bot runtime.
- web_dashboard_ts: Next.js staff dashboard for live moderation and verification workflows.

Current production data store for dashboard is PostgreSQL.

SQLite mode remains available for local/single-node operation.

Optional shared SQLite path:

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

## First-Time Setup (Windows)

Use this path if you are running the project for the first time.

### Prerequisites

Install these first:

- Node.js 20 LTS (includes npm)
- Git
- Rust toolchain (optional, only needed for Rust sidecar mode)

### Bot Only (Recommended First Run)

1. Open PowerShell and move to the repo root:

```powershell
Set-Location E:\fluxer_bot
```

2. Move into the bot folder and install dependencies:

```powershell
Set-Location .\bot_js
npm install
```

3. Create your environment file:

```powershell
copy .env.example .env
```

4. Edit `bot_js/.env` and set at least:

- `FLUXER_BOT_TOKEN` to your real bot token

5. Start the bot:

```powershell
npm run start
```

6. Verify startup:

- Look for a log like `ready as <botname>`
- Test a command in your server (example: `/join`, `/stats`)

### Rust Sidecar Mode (Optional)

If you want Rust-backed raid scoring:

1. Install Rust with rustup.
2. From `bot_js`, run:

```powershell
npm run start:rust:all
```

This command builds the sidecar, starts it, and starts the bot.

### Dashboard (Optional)

Run this in a second terminal while the bot is running:

```powershell
Set-Location E:\fluxer_bot\web_dashboard_ts
npm install
copy .env.example .env
npm run dev
```

Then open: http://127.0.0.1:3000/login

For dashboard login to work, fill required OAuth and session values in `web_dashboard_ts/.env`:

- `FLUXER_CLIENT_ID`
- `FLUXER_CLIENT_SECRET`
- `FLUXER_REDIRECT_URI`
- `DASHBOARD_SESSION_SECRET`
- `FLUXER_BOT_TOKEN`
- `DASHBOARD_DB_DRIVER=postgres` plus `POSTGRES_*`

### Common Mistake

`npm run start:rust:all` must be run from the `bot_js` directory, not the repo root.

## Important Defaults

- Bot DB path: bot_js/data/warnings.db
- Bot word list: bot_js/data/words.json
- Dashboard DB default driver: postgres
- Dashboard SQLite path (optional): ../bot_js/data/warnings.db

## Notes

- The old Python bot code and requirements have been removed from this workspace.
- For production deployments, use `DASHBOARD_DB_DRIVER=postgres` with `POSTGRES_*` variables.
- SQLite remains optional for local/single-node usage: `DASHBOARD_DB_DRIVER=sqlite` and `BOT_DB_PATH=../bot_js/data/warnings.db`.
