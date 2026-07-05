# Security & known limitations

wikiAgent has been through a deep adversarial review (correctness, security,
prompt-injection, concurrency/scale, and the Node client). Most findings are
fixed in code (see CHANGELOG `0.3.2`). This document records the issues that are
**inherent or architectural** — mitigated but not fully closable without extra
infrastructure — plus operational guidance.

## Threat model

Facts stored in `wiki_knowledge` come from **untrusted sources** (WhatsApp
messages, Markdown files, conversation text). Anything an attacker can get
ingested becomes searchable content that is later fed to Claude/ChatGPT.

## Fixed in code (highlights)

- **Prompt-injection hardening** — extractor & classifier now put instructions
  in a `system` role and fence untrusted text in `<transcript>…</transcript>`
  data blocks; forged role-lines are neutralized.
- **MCP parity** — `add_wiki_fact` now runs the privacy filter; the MCP server
  is rate-limited; internal errors no longer leak to clients.
- **Correctness** — BM25 now tokenizes Vietnamese (was ASCII-only); point ids
  include topic (distinct facts no longer overwrite); embed batches are ordered
  & chunked; consolidation ranks survivors by **source-trust tier**, not
  self-asserted confidence.
- **Client** — the Baileys agent retries on POST failure, backs off on
  reconnect, masks phone numbers in logs, and filters Channels/broadcasts.
- Constant-time token comparison; request-body size caps; safe env parsing.

## Accepted / architectural limitations

1. **Rate limiting is per-process.** The in-memory limiter caps a single worker.
   Under `uvicorn --workers N`, the effective limit is `N×`. For a hard global
   limit, front the API with a gateway/Redis-backed limiter. Single-worker
   deploys (the default here) are unaffected.

2. **Second-order (stored) prompt injection is inherent.** A poisoned fact's
   `content` becomes model-visible context when Claude/ChatGPT call
   `search_wiki`. Mitigations in place: provenance (`source`, `confidence`) is
   returned so the client can distrust `source=whatsapp/file`; the privacy
   filter and system/data split reduce first-order poisoning. Operational
   guidance: treat retrieved facts as **data, not instructions**, and be wary of
   co-locating destructive tools (`delete_wiki_fact`) with the read tool in an
   agent that auto-executes. Consider gating destructive MCP tools behind
   human confirmation.

3. **The privacy keyword filter is secret-leak prevention, not an injection
   control, and is bypassable** (encodings, homoglyphs, secrets split across
   messages). Keep genuinely sensitive material out of ingested sources; don't
   rely on the filter as a security boundary.

4. **Consolidation does not scale to very large corpora.** The nightly job loads
   all vectors into RAM and does an O(n²)-per-topic cosine scan. Fine for tens
   of thousands of facts; beyond that, move to batched/ANN dedup. It is a
   read-mostly nightly job and dry-run by default.

5. **No retry/backoff on outbound LLM/Qdrant calls.** A transient 429/5xx drops
   that single item (ingest is idempotent, so re-ingest recovers). Add a retry
   layer if upstream flakiness becomes an issue.

6. **Read and destructive (`DELETE`) operations share one token.** For
   least-privilege, issue read-only clients a token that cannot delete — a
   future enhancement (e.g. a separate `WIKI_ADMIN_TOKEN` gating destructive
   ops). Until then, treat the API token as write-capable.

## Operational recommendations

- Run behind TLS; never expose the API without the bearer token.
- Prefer a single worker, or add a shared-store rate limiter for multi-worker.
- Give the dashboard/read clients a token you are willing to let delete facts
  (see #6), or restrict them at the proxy.
- Commit a lockfile for the Node client for reproducible installs.
