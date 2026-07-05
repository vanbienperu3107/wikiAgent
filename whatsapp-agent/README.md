# whatsapp-agent

Client side of "Phase 3" of a personal knowledge system.

It connects to your WhatsApp account (via [Baileys](https://github.com/WhiskeySockets/Baileys)),
listens for **incoming personal (non-group) messages**, buffers them per conversation,
and — after a quiet period — posts each thread to a knowledge-extraction REST endpoint
(`POST ${WIKI_API_URL}/ingest/whatsapp`) for storage.

## What it captures

- Only **personal 1:1 chats** — group chats (`@g.us`) and status broadcasts are skipped.
- Only **messages from other people** — your own outgoing messages (`fromMe`) are skipped.
- Messages are **buffered per thread** and flushed after `WA_WINDOW_MS` (default **5 minutes**)
  of silence in that thread, then the buffer is cleared.

Everything is in-memory (a `Map` of buffers + debounce timers). No database.

## Setup

```bash
npm install
cp .env.example .env   # then edit WIKI_API_URL / WIKI_AUTH_TOKEN
npm start
```

On first run a **QR code** is printed to the terminal — scan it from
WhatsApp on your phone (Settings → Linked Devices → Link a Device).
Auth is persisted under `./auth/`, so subsequent runs reconnect automatically.

## Configuration

| Env var           | Default                 | Description                                   |
| ----------------- | ----------------------- | --------------------------------------------- |
| `WIKI_API_URL`    | (required)              | Base URL of the ingest API                    |
| `WIKI_AUTH_TOKEN` | (required)              | Bearer token sent as `Authorization` header   |
| `WA_WINDOW_MS`    | `300000` (5 min)        | Debounce window per thread before flushing     |
