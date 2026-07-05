# wikiAgent Dashboard

A single, self-contained web dashboard for the **wikiAgent REST API**. No build step,
no npm, no framework, no external CDN — just one static `index.html` (inline CSS +
vanilla JS). The only network calls it makes are to the wikiAgent API you point it at.

## Use it

It's a static file. Two ways to open it:

1. **Open directly** — double-click `index.html`, or open it in your browser via
   `file://…/dashboard/index.html`. This works fine against a **localhost** API.
2. **Serve it** (recommended if you hit any file:// quirks):

   ```bash
   cd dashboard
   python3 -m http.server 8080
   # then visit http://localhost:8080
   ```

Then in the settings bar at the top:

- **API base URL** — default `http://localhost:8010`
- **Bearer token** — your `WIKI_AUTH_TOKEN`

Click **Save**. Both values are persisted in `localStorage`, so you only enter them once
per browser. Every request is sent with `Authorization: Bearer <token>`.

## What it does

- **Topics** — `GET /wiki/topics`: lists each topic with its fact count and the
  source badges (conversation / file / whatsapp / manual) that contributed to it.
- **Search** — `GET /wiki/search?q=…&limit=10`: type a query and hit Search. Toggle
  **hybrid (RAG 2.0)** to add `&hybrid=true`. Each result card shows the content,
  topic, a color-coded source badge, confidence, `updated_at`, and the similarity
  score, plus a **Delete** button (`DELETE /wiki/fact/{id}`, then re-runs the search).
- **Add fact** — `POST /wiki/fact`: topic, content, comma-separated tags, and a
  confidence value (0–1).

HTTP errors are reported inline under each view — e.g. a `401` shows
"Unauthorized — check token", a network failure hints at CORS / the API being down.

## CORS notes

- The **MCP HTTP server** already allows all origins, so cross-origin calls to it work
  out of the box.
- For the **REST API (`wiki_agent/app.py`, FastAPI)**, if you serve this dashboard from
  a *different* origin than the API (e.g. `http://localhost:8080` → `http://localhost:8010`),
  the browser will enforce CORS. You may need to add FastAPI's CORS middleware to
  `app.py`:

  ```python
  from fastapi.middleware.cors import CORSMiddleware
  app.add_middleware(
      CORSMiddleware,
      allow_origins=["*"],        # or restrict to your dashboard origin
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```

- **Opening the file locally against a localhost API generally just works** — same-machine
  localhost requests usually don't trip CORS for simple GETs, and adding the middleware
  above covers the rest (POST/DELETE with an `Authorization` header trigger a preflight).

## Endpoints used

| View     | Call |
|----------|------|
| Topics   | `GET /wiki/topics` |
| Search   | `GET /wiki/search?q=…&limit=10[&hybrid=true]` |
| Delete   | `DELETE /wiki/fact/{id}` |
| Add fact | `POST /wiki/fact` (`{topic, content, tags[], confidence}`) |

> Note: `GET /wiki/topics` and `GET /wiki/search` are implemented in the current
> `app.py`. The `hybrid` flag (RAG 2.0), `POST /wiki/fact`, and `DELETE /wiki/fact/{id}`
> are the write/RAG endpoints this dashboard targets; if your API build doesn't expose
> them yet, those actions will surface an inline HTTP error (e.g. 404/405) until they land.
