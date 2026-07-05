# Changelog

## 0.2.0 — Phases 3–5 + manual CRUD (all sources + retrieval + consolidation)

- **RAG 2.0** (`rag.py`, Phase 4): `hybrid_search()` — dense vector pool +
  self-contained BM25 (k1=1.5, b=0.75) fused with Reciprocal Rank Fusion (k=60),
  optional recency re-rank (`beta>0`). Exposed via `GET /wiki/search?hybrid=true`
  and the `search_wiki` MCP tool `hybrid` flag.
- **Consolidation** (`consolidation.py` + `scripts/run_consolidation.py`, Phase 5):
  near-duplicate grouping (union-find, cosine ≥ threshold), Haiku contradiction
  detection, and status flagging (active/obsolete — never deletes). Dry-run by
  default; nightly runner scrolls Qdrant with vectors.
- **Manual fact CRUD** (`fact_crud.py`): `add_fact` / `delete_fact` /
  `update_fact` with deterministic idempotent ids. REST `POST /wiki/fact`,
  `DELETE /wiki/fact/{id}`; MCP `add_wiki_fact`, `delete_wiki_fact`.
- **Baileys client** (`whatsapp-agent/`, Node.js): connects to WhatsApp, skips
  groups, buffers per thread (5-min debounce), POSTs to `/ingest/whatsapp`.
- Tests: +40 offline cases (rag, consolidation, fact_crud). Full suite 56/56 green.

## Unreleased — Phase 3 (server-side): WhatsApp source

- `whatsapp.py` — WhatsApp thread → contact blacklist + privacy filter
  (deterministic) → Qwen 7B keep/skip classifier (DeepInfra, OpenAI fallback) →
  Haiku extraction only when kept → store with `source="whatsapp"` (content-hash
  dedup). The cheap classifier gates the expensive extractor for cost control.
- REST: `POST /ingest/whatsapp` (messages, thread_id, sender).
- Config: `DEEPINFRA_API_KEY`, `WHATSAPP_CLASSIFIER_MODEL`,
  `WHATSAPP_CONTACT_BLACKLIST`.
- Tests: blacklist, classification parsing, and pipeline gating (skip small talk,
  short-circuit blacklisted/sensitive) — all offline via mocks.

## 0.1.0 — Phase 1: Wiki Knowledge Layer

Initial scaffold of the multi-source knowledge layer.

- `knowledge_extractor.py` — privacy filter → Haiku fact extraction (OpenAI
  fallback) → embed + deterministic upsert into `wiki_knowledge`.
- `qdrant_helper.py` — `ensure_wiki_collection()`, upsert, semantic search with
  topic/source filters, and topic scroll aggregation.
- `wiki_search.py` — `search_wiki()` and `list_wiki_topics()`.
- REST API (`app.py`): `/ingest/conversation`, `/ingest/file`, `/wiki/search`,
  `/wiki/topics`, `/health`.
- MCP HTTP server (`mcp_server.py`): `search_wiki`, `list_wiki_topics`.
- Tests covering privacy filter, JSON parsing robustness, deterministic ids,
  10-conversation extraction, and the query layer (LLM/Qdrant mocked).
- Docker, docker-compose, CI workflow, `.env.example`.
