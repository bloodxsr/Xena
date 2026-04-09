# Fluxer Moderation Platform

This repository contains two tightly integrated services:

1. A Python moderation bot built on `fluxer.py`.
2. A Next.js + TypeScript web dashboard used by staff for live guild control and verification operations.

The dashboard and bot share the same SQLite data model so operators can run command-driven workflows in chat and web-driven workflows in the console without splitting state.

## What The Project Does

### Bot service (`bot/`)

- Automated moderation for blacklisted content.
- Warning escalation pipeline with auto-kick at configured thresholds.
- Staff moderation commands (kick, ban, mute, unmute, purge, role actions).
- Security and anti-raid controls with join risk scoring.
- Join verification queue with approve/reject outcomes.
- TOTP-protected privileged commands with a 30-day re-auth window.
- Gemini-backed AI assistant commands (`ask`, `joke`, `aistatus`).
- Welcome/help experience with server resource hints.
- Optional Flask keep-alive health endpoint.

### Dashboard service (`web_dashboard_ts/`)

- Fluxer OAuth2 login flow with signed state validation.
- Session cookie auth for staff console access.
- Shared-guild filtering: only guilds both the user and bot can access.
- Real-time guild operations through Fluxer bot API:
  - member moderation
  - role assignment/removal
  - raid gate toggles
  - warnings updates
  - verification approvals/rejections
  - channel purge
  - blacklist management
- Guild profile overrides (dashboard card name/icon).

## Repository Structure

```text
fluxer_bot/
  README.md
  requirements.txt
  .gitignore
  bot/
    main.py
    keep_alive.py
    words.py
    token.txt                 # local secret, ignored
    google.txt                # local secret, ignored
    warnings.db               # runtime DB, ignored
    cogs/
      admin.py
      ai.py
      moderation.py
      security.py
      welcome.py
    database/
      db.py
    utils/
      autodelete.py
      checks.py
      embeds.py
      paginator.py
      raid_signals.py
      totp_auth.py
      word_store.py
  web_dashboard_ts/
    README.md
    .env.example
    .gitignore
    package.json
    next.config.mjs
    src/
      app/
      components/
      lib/
```

## Tech Stack

- Bot: Python 3.11+, `fluxer.py`, SQLite, Flask (optional), Google Gemini SDK.
- Dashboard: Next.js 14 App Router, TypeScript, React 18, `better-sqlite3`.
- Shared persistence: `bot/warnings.db`.

## Quick Start

### 1. Install bot dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure secrets for bot runtime

Choose one of these token methods:

- Set environment variable `FLUXER_BOT_TOKEN` (recommended).
- Or place token text in `bot/token.txt`.

For AI features:

- Set `GOOGLE_API_KEY`/`GEMINI_API_KEY`, or
- place key in `bot/google.txt`.

### 3. Run the bot

```powershell
python bot/main.py
```

### 4. Run dashboard

```powershell
cd web_dashboard_ts
npm install
copy .env.example .env
npm run dev
```

Open `http://127.0.0.1:3000/login`.

## Bot Runtime Configuration

The bot reads `.env` values via `python-dotenv` plus fallback local files.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLUXER_BOT_TOKEN` | none | Primary bot token source. |
| `BOT_TOKEN` / `TOKEN` | none | Alternate bot token names. |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | none | Gemini API key for AI commands. |
| `BOT_PREFIXES` | `/,!` | Comma-separated command prefixes. |
| `MAX_WARNINGS` | `4` | Auto-moderation warning threshold before escalation. |
| `TOTP_ISSUER` | `FluxerBot` | Issuer label used in authenticator apps. |
| `AUTO_DELETE_ENABLED` | `true` | Enables auto-delete for configured command messages. |
| `AUTO_DELETE_DELAY_SECONDS` | `10` | Delay before deleting command/reply messages. |
| `AUTO_DELETE_COMMANDS` | built-in list | CSV override for commands that auto-delete. |
| `AI_MODEL_NAME` | `gemini-2.5-flash` | Gemini model identifier. |
| `AI_RATE_LIMIT_SECONDS` | `5` | Cooldown per user for AI commands. |
| `AI_TIMEOUT_SECONDS` | `30` | Timeout for AI generation calls. |
| `AI_MAX_RESPONSE_LENGTH` | `1500` | Truncation cap for AI responses. |
| `AI_MAX_QUESTION_LENGTH` | `1500` | Input limit for `/ask`. |
| `ABOUT_TEXT` | built-in text | `/aboutserver` response content. |
| `PERKS_TEXT` | built-in text | `/perks` response content. |
| `ENABLE_UPTIME_SERVER` | `false` | Starts Flask health service when enabled. |
| `UPTIME_HOST` | `0.0.0.0` | Flask keep-alive bind host. |
| `UPTIME_PORT` | `8080` | Flask keep-alive bind port. |

## Dashboard Configuration

Dashboard env values are loaded in `web_dashboard_ts/src/lib/env.ts`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_BASE_URL` | `http://127.0.0.1:3000` | Canonical origin used for redirects/callbacks. |
| `FLUXER_WEB_BASE` | `https://web.fluxer.app` | Fluxer web base for OAuth authorize URL. |
| `FLUXER_API_BASE` | `https://api.fluxer.app` | Fluxer API base. |
| `FLUXER_REDIRECT_URI` | derived | Explicit OAuth callback URI override. |
| `FLUXER_CLIENT_ID` | none | OAuth app client id. |
| `FLUXER_CLIENT_SECRET` | none | OAuth app secret. |
| `FLUXER_OAUTH_SCOPE` | `identify guilds` | OAuth scopes requested during login. |
| `SESSION_SECRET` | dev fallback | HMAC secret for session and OAuth state signing. |
| `DATABASE_PATH` | `bot/warnings.db` | SQLite path used by dashboard. |
| `FLUXER_BOT_TOKEN` / `BOT_TOKEN` | none | Bot token for live guild API actions. |
| `FLUXER_DASHBOARD_KEY` | none | Optional extra key required at login. |
| `FLUXER_ALLOWED_GUILD_IDS` | empty set | Optional guild whitelist. |
| `FLUXER_ADMIN_USER_ID` | `1` | Reserved admin metadata value. |
| `FLUXER_ADMIN_USERNAME` | `Fluxer Admin` | Reserved admin metadata value. |

## Security Model

### TOTP guardrail (bot commands)

- Admin and moderation command paths are TOTP-gated.
- Staff enroll with `/totpsetup`.
- Staff refresh privileged access with `/totpauth <code>`.
- Access window is persisted for 30 days.
- Commands may also accept inline `--totp <code>` verification.

### OAuth and session hardening (dashboard)

- OAuth state token is HMAC-signed.
- Callback accepts either matching state cookie or valid signed state.
- Dashboard session token is signed and expiration checked.
- Login route normalizes host origin to `APP_BASE_URL`.

### Snowflake ID handling

- Guild/user/role/channel IDs are treated as strings end-to-end in web flows.
- This avoids JavaScript integer precision loss for large Snowflakes.

## Bot Command Reference

Prefix-based command execution supports `/` and `!` by default.

### Moderation commands

- `/warnings [user]`
- `/kick <user> <reason> --confirm`
- `/ctxkick <reason> --confirm`
- `/ban <user> <reason> --confirm`
- `/ctxban <reason> --confirm`
- `/mute <user> [minutes] [reason]`
- `/ctxmute [minutes] [reason]`
- `/unmute <user> [reason]`
- `/ctxunmute [reason]`
- `/purge <count>`

### Admin commands

- `/addbadword <word_or_phrase>`
- `/removebadword <word_or_phrase>`
- `/viewbadwords [page]`
- `/viewbadwordsnext`
- `/viewbadwordsprev`
- `/reloadwords`
- `/addrole <user> <role>`
- `/removerole <user> <role>`
- `/ctxaddrole <role>`
- `/ctxremoverole <role>`
- `/setlogchannel <channel_id>`
- `/setwelcomechannel <channel_id>`
- `/setresourcechannels <rules> <chat> <help> <about> <perks>`
- `/setroles AdminRole | ModRole`
- `/setsyncmode <global|guild> [guild_id]`
- `/serverconfig`
- `/adminhelp`

### Security and anti-raid commands

- `/totpsetup`
- `/totpreset`
- `/totpauth <6-digit-code>`
- `/totpdisable <6-digit-code>`
- `/setverificationurl <https://...|off>`
- `/setraidsettings <threshold> <join_rate_threshold> <window_seconds> <gate_duration_seconds> <timeout|kick>`
- `/setraiddetection <on|off>`
- `/raidgate <on|off|status> [duration_seconds]`
- `/pendingverifications [limit]`
- `/verifyjoin <user>`
- `/rejectjoin <user> [reason]`
- `/raidsnapshot [limit]`

### AI and utility commands

- `/ask <question>`
- `/joke`
- `/aistatus`
- `/help [category]`
- `/helpmenu`
- `/aboutserver`
- `/perks`

## Dashboard Route Reference

### Page routes

- `/` homepage.
- `/login` Fluxer OAuth2 login screen.
- `/oauth/callback` OAuth callback handler.
- `/dashboard` server selection for manageable guilds.
- `/guild/<guild_id>` full guild control console.
- `/verify/<guild_id>` member self-verification page.
- `/logout` clears session.

### API routes

- `GET|POST /api/auth/login`: starts OAuth login and sets state cookies.
- `GET /api/guild/<guild_id>/config`: read guild config.
- `POST /api/guild/<guild_id>/config`: update guild config.
- `GET /api/guild/<guild_id>/profile`: read guild profile override.
- `POST /api/guild/<guild_id>/profile`: update guild profile override.
- `GET /api/guild/<guild_id>/roles`: fetch live guild roles from Fluxer.
- `POST /api/guild/<guild_id>/member-actions`: kick/ban/unban/mute/unmute/add_role/remove_role.
- `POST /api/guild/<guild_id>/purge`: delete channel messages (bulk then fallback).
- `POST /api/guild/<guild_id>/raidgate`: enable/disable gate.
- `GET /api/guild/<guild_id>/pending`: list pending verification queue.
- `POST /api/guild/<guild_id>/verifications/<user_id>`: approve/reject verification.
- `GET /api/guild/<guild_id>/warnings`: list warning records.
- `GET|POST /api/guild/<guild_id>/warnings/<user_id>`: get/set/increment/reset warning count.
- `GET /api/guild/<guild_id>/moderation-logs`: list moderation logs.
- `GET /api/guild/<guild_id>/join-events`: list recent join telemetry.
- `GET|POST /api/guild/<guild_id>/blacklist`: list/add/remove/replace blacklist words.

## Shared Database Tables

Core tables initialized by the bot:

- `warnings`
- `warning_events`
- `moderation_logs`
- `guild_config`
- `totp_secrets`
- `raid_state`
- `verification_queue`
- `join_events`

Dashboard adds local extension table:

- `guild_profiles`

## Troubleshooting

### `oauth_state_mismatch`

- Start login again from `/login`.
- Ensure `APP_BASE_URL` matches the host you are browsing.
- Keep `next.config.mjs` `allowedDevOrigins` aligned for `127.0.0.1` and `localhost`.

### `No manageable guilds found`

- Confirm your account has Administrator, Manage Server, or Moderate Members permissions.
- Ensure bot is in the target guild.
- Check optional `FLUXER_ALLOWED_GUILD_IDS` filtering.

### Roles or moderation actions return 502

- Verify `FLUXER_BOT_TOKEN` is configured in dashboard env.
- Verify bot token has access to that guild.
- Confirm Snowflake IDs are sent as plain numeric strings.

### Bot starts but AI commands fail

- Confirm `GOOGLE_API_KEY`/`GEMINI_API_KEY` or `bot/google.txt` exists.
- Run `/aistatus` to validate model availability.

## Important Notes About Fluxer Command Model

- `fluxer.py` currently provides prefix command + cog APIs.
- Native slash command trees and interactive button flows are not used here.
- Context-like command behavior is implemented via reply-target commands (`ctx*`).

## Operational Recommendations

- Keep all secrets in `.env` or ignored local files only.
- Rotate any token that has ever been committed accidentally.
- Back up `bot/warnings.db` before schema-affecting changes.
- Treat join telemetry and moderation logs as sensitive data.
