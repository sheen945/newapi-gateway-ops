# newapi-gateway-ops

> Day-to-day operations and troubleshooting for a self-hosted New API / one-api gateway — covering "model unavailable / calls getting slow / intermittent failures / channel 429 rate limiting / missing models in the list / add & remove models / bulk channel edits / quota checks / API token management / safe handling of sk- keys".

[中文版](README.md)

## Overview

This is a WorkBuddy / Claude Code skill that distills real-world experience from maintaining a self-hosted new-api (one-api family) LLM gateway over the long run. Its core is not "how to call the API" — it is three things:

1. **A standard diagnostic method that separates upstream rate limiting from local misconfiguration** — failed requests never land in `logs`, so "clean logs" proves nothing. The truth lives in the retry counts of *successful* records.
2. **A three-place coordinated model-change procedure (channels + abilities + options)** — editing `channels.models` alone is not enough; miss one place and you get `503 No available channel for model`.
3. **A four-piece token-safety toolkit** — `sk-` keys travel from gateway to destination entirely through script pipelines; the AI only ever sees redacted output, and plaintext keys never enter the conversation, logs, or command arguments.

**Trigger phrases**: model unavailable, slow responses, intermittent failures, channel rate limiting, 429, missing models, add/remove models, bulk channel changes, check balance, manage tokens, copy a key, inject key into config, scheduled health checks, gateway ops.

## Features

- **Three-step rate-limit diagnosis**: Step 1 — compute the first-try hit rate from `admin_info.use_channel` in the `other` field of successful log rows (below ~90% means upstream throttling). Step 2 — group by hour to see whether it is persistent. Step 3 — use `scripts/probe_channels.py` to hit upstream base URLs directly, bypassing the gateway, and classify the limit type with decision rules: "long cooldown / account-level penalty" vs "minute-level RPM/TPM sliding window" vs "token-volume limit (`EndpointTPMExceeded`) vs request-count limit (`RpmRateLimitExceeded`)". Bonus: `GET /api/channel/cooldowns` reveals gateway-side per-model cooldowns.
- **Three-place coordinated model changes**: `channels.models` (what a channel declares), `abilities` (the routing table, primary key `("group", model, channel_id)` — `group` is a SQL reserved word and must be double-quoted), and three pricing rows in `options` (`ModelRatio` / `CompletionRatio` / `CacheRatio`). The recommended path is the management API (hot reload): after updating `channels.models`, New API rebuilds abilities automatically with no downtime. Four mandatory verifications follow: re-read the channel, count abilities rows, check `/v1/models` with an sk- token, and make one real call.
- **Complete model-removal checklist**: channels.models → abilities → the three pricing rows in options → client-side configs (e.g. models.json) — nothing skipped.
- **Dynamic retry tuning**: when upstream throttles frequently, adjust `GetDynamicRetryTimes()` (available channels ÷ 2, rounded up, capped at 8) — with the trade-offs spelled out: more retries mean longer waits and possibly extended penalties, and no retry policy helps when *every* channel is returning 429.
- **Backend change → production runbook**: back up source → `go build` → md5-verify the artifact → taskkill the service → back up the old exe and swap in the new one → start via the scheduled task → four acceptance checks (port LISTENING, `/api/status` 200, `/burner` 200, `MainWindowHandle=0`). If the frontend changed, rebuild `web/dist` with rsbuild (not vite) first — dist is embedded into the exe via go:embed. Frontend first, backend second.
- **Token-safety toolkit**: `copy-key.js` (key flows only through a pipe into the clipboard), `inject-key.js` (atomic placeholder `__NEWAPI_TOKEN_<id>__` replacement in config files — never read the target file directly), `exec-token.js` (placeholder substitution for CLI commands, output auto-redacted, exit code passed through), and `api.js` (unified management API caller with automatic redaction of `/api/token*` responses).
- **Token management**: list tokens (key field redacted in output), create tokens (quota unit is USD × 500000; after creation only the ID is reported — never fetch the key again), and change groups (GET all fields first, then PUT the full object back).

## How It Works / Tech Stack

- **Skill mechanism**: carried by `SKILL.md`, loaded by the AI via trigger-phrase matching. Diagnosis, model changes, and the deployment runbook are all given as copy-paste-runnable SQL / Python / Bash snippets rather than abstract advice.
- **Direct database access**: the gateway's real database is `<gateway install dir>/one-api.db` (SQLite). Beware: a same-named file in the workspace is usually a 0-byte placeholder. Backups must use sqlite3's backup API, not a raw file copy. `logs.type`: 2 = consumption, 3 = management, 7 = login. SQLite has no `LEFT()` — use `substr()`.
- **Two token types, different jobs**: `/v1/*` endpoints take a user token (`sk-` prefix); `/api/*` management endpoints take the `access_token` of `users.id=1`. Do not mix them up — calling `/v1/models` with the admin access_token returns 401, which is easily misread as "the model list is empty".
- **Script layer**: pure zero-dependency JS under `scripts/` (run directly with node), with configuration resolved in the order: environment variables > skill-directory `.env` > project-root `.env` (`NEWAPI_BASE_URL` / `NEWAPI_ACCESS_TOKEN` / `NEWAPI_USER_ID`). `sanitize.js` provides unified redaction (sk- keys, Bearer tokens, `user:pass@host` in connection strings, sensitive field names) — a risk-reduction measure, not a formal parser.
- **Windows / Git Bash pitfalls**: MSYS path conversion mangles `/PID` and `/FI` in `taskkill` / `tasklist` — write `//PID`, `//FI`; likewise `/api/...` path arguments get eaten when calling the scripts — prefix with `MSYS_NO_PATHCONV=1`; and PowerShell builtins like `Out-File` / `type` do not exist in Git Bash.

## Installation & Usage

Copy this repository's contents into your skills directory, keeping the folder name `newapi-gateway-ops`:

- WorkBuddy / CodeBuddy: `~/.workbuddy/skills/newapi-gateway-ops/`
- Claude Code: `~/.claude/skills/newapi-gateway-ops/`

Restart the session and the skill matches automatically via trigger phrases. When using it:

1. **Always start by confirming the environment**: gateway address (default `http://127.0.0.1:3000`), the real database location, and the token type. Never hardcode.
2. Token-safety scenarios (copy / inject into config / CLI use / leak scanning) → read `docs/token-security.md` and follow it. The scripts need `.env` configuration; on `[CONFIG_MISSING]`, stop retrying and fill in the access_token from the key vault.
3. When calling `scripts/api.js` and friends from Git Bash, always prefix with `MSYS_NO_PATHCONV=1`.

## Project Structure

```
newapi-gateway-ops/
├── README.md                 # Chinese README
├── README_EN.md              # This file (English)
├── SKILL.md                  # Skill main file: 3-step diagnosis, 3-place model changes, deploy runbook, red lines, pitfalls
├── docs/
│   └── token-security.md     # Token-safety module: 6 security rules + 3 script usages + token management + redaction rules
├── scripts/
│   ├── probe_channels.py     # Per-channel direct probing (model/interval/channel filters); classifies rate-limit types
│   ├── api.js                # Unified management API caller (auto-redacts /api/token* responses)
│   ├── copy-key.js           # Copy a key to the clipboard (pipe only, never prints plaintext)
│   ├── inject-key.js         # Config-file placeholder injection (--scan redacted view + atomic write-back)
│   ├── exec-token.js         # CLI placeholder substitution (redacted output + exit-code passthrough)
│   ├── env.js                # Config-loading support module (not called directly)
│   ├── fetch-key.js          # Key-fetching support module (not called directly)
│   └── sanitize.js           # Unified redaction rules support module (not called directly)
├── .env.example              # Config template (NEWAPI_BASE_URL / NEWAPI_ACCESS_TOKEN / NEWAPI_USER_ID)
└── .gitignore
```

## Notes

- **Back up before touching the database or any config**, with dates in filenames; use sqlite3's backup API, not raw copies.
- ⛔ **Never include per-call billed channels (image/video generation) in scheduled health checks** — every probe is real money.
- ⛔ **Never put 429 into the channel auto-disable list** (`AutomaticDisableStatusCodes` should only contain auth errors like 401). Blacklisting on minute-level 429s triggers a "retry × blacklist" avalanche.
- ⛔ **`sk-` keys never enter conversations / logs / command arguments**; never touch "auto-test" scheduled switches on your own — ask first and explain the cost risk.
- This skill targets a **self-hosted** new-api / one-api gateway, not the official SaaS. The token-safety module was adapted (and localized into Chinese) from the community skill [QuantumNous/skills](https://github.com/QuantumNous/skills).

## License

MIT

## Author

sheen945
