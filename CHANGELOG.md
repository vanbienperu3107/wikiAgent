# Changelog

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
