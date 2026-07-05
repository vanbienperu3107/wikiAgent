# whatsapp-agent

Client side of "Phase 3" of a personal knowledge system.

It connects to your WhatsApp account (via [Baileys](https://github.com/WhiskeySockets/Baileys)),
listens for **incoming personal (non-group) messages**, buffers them per conversation,
and — after a quiet period — posts each thread to a knowledge-extraction REST endpoint
(`POST ${WIKI_API_URL}/ingest/whatsapp`) for storage.

## What it captures

- Only **personal 1:1 chats** — group chats (`@g.us`), status/broadcasts (`@broadcast`)
  and WhatsApp Channels (`@newsletter`) are skipped.
- Only **messages from other people** — your own outgoing messages (`fromMe`) are skipped.
- Messages are **buffered per thread** and flushed after `WA_WINDOW_MS` (default **5 minutes**)
  of silence in that thread. The buffer is cleared **only after a successful POST**; a failed
  POST keeps the buffer so the next window (or shutdown) retries it. Buffers are capped at
  500 messages per thread (oldest dropped) to bound memory when the endpoint is down.

Everything is in-memory (a `Map` of buffers + debounce timers). No database.

Logs never include message text or full contact numbers — JIDs are masked to a
short suffix (e.g. `***1234@s.whatsapp.net`).

Requires **Node 18+** (uses the global `fetch`).

## Setup

```bash
npm install
cp .env.example .env   # then edit WIKI_API_URL / WIKI_AUTH_TOKEN
npm start
```

> Commit the generated `package-lock.json` for reproducible installs.

On first run a **QR code** is printed to the terminal — scan it from
WhatsApp on your phone (Settings → Linked Devices → Link a Device).
Auth is persisted under `./auth/`, so subsequent runs reconnect automatically.

## Configuration

| Env var           | Default                 | Description                                   |
| ----------------- | ----------------------- | --------------------------------------------- |
| `WIKI_API_URL`    | (required)              | Base URL of the ingest API                    |
| `WIKI_AUTH_TOKEN` | (required)              | Bearer token sent as `Authorization` header   |
| `WA_WINDOW_MS`    | `300000` (5 min)        | Debounce window per thread before flushing     |
