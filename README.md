# wikiAgent — Wiki Knowledge Layer

> **Phase 1 of Roadmap 3.0 — Personal AI Knowledge System**
> *Từ mem0custom đến multi-source knowledge base tự động.*
>
> Biến mọi cuộc hội thoại AI (và sau này là file Markdown & WhatsApp) thành
> **knowledge có cấu trúc, tìm kiếm được**, để Claude & ChatGPT truy cập qua
> MCP/REST từ bất kỳ client nào.

`wikiAgent` là **trung tâm kết hợp nhiều nguồn (multi-source)**: nó gom
knowledge từ 3 nguồn khác nhau vào **một** collection Qdrant duy nhất
(`wiki_knowledge`) và phục vụ mọi AI client qua một API thống nhất. Thiết kế để
đứng cạnh [`agentMem0`](https://github.com/vanbienperu3107/agentMem0) và **dùng
lại Qdrant + API keys sẵn có — không thêm datastore, không thêm service.**

Production: `claude.hangocthanh.io.vn` · Tác giả: Hà Ngọc Thanh · `vanbienperu3107`

---

## Ý tưởng

`agentMem0` lưu transcript nguyên bản và quản lý facts rời (mem0). `wikiAgent`
bổ sung lớp còn thiếu: **knowledge có cấu trúc, khử trùng lặp, đa nguồn**, với
schema tường minh (`topic`, `content`, `source`, `tags`, `confidence`,
timestamps) mà mọi AI client đều query được.

```
Nguồn 1: AI conversation  ─┐
Nguồn 2: Markdown file     ├─►  knowledge_extractor / ingest  ─►  wiki_knowledge (Qdrant)
Nguồn 3: WhatsApp chat     ─┘                                          │
                                                                       ▼
                                        search_wiki · list_wiki_topics  (MCP + REST)
                                                                       │
                             Claude.ai · ChatGPT · Claude Code · Custom GPT
```

## Nguyên tắc thiết kế (từ Roadmap 3.0)

- **Không thêm service** — chạy trên Qdrant bạn đã có.
- **LLM chỉ dùng cho judgment** — quyết định *cái gì là fact đáng tái sử dụng*.
  Routing / filter / dedup đều là code tất định.
- **Privacy-first** — filter keyword nhạy cảm *trước* mọi lời gọi LLM và trước
  khi lưu.
- **Idempotent** — `uuid5(content)` tất định ⇒ re-ingest cùng một fact là ghi đè
  tại chỗ, không nhân bản.
- **Đo trước khi tối ưu** — RAG 2.0 (Phase 4) chỉ làm sau khi thu 50 query thực tế.

## Kiến trúc

```
wiki_agent/
├── config.py             # gom toàn bộ env var
├── embeddings.py         # OpenAI text-embedding-3-small (1536 dims)
├── qdrant_helper.py      # collection wiki_knowledge: ensure / upsert / search / scroll
├── knowledge_extractor.py# privacy filter → Haiku extract → embed + store
├── wiki_search.py        # search_wiki() + list_wiki_topics()
├── app.py                # REST API (FastAPI): ingest + query
└── mcp_server.py         # MCP HTTP server (Streamable HTTP, JSON-RPC)
```

### Schema payload `wiki_knowledge`

| field        | kiểu       | ví dụ                            |
|--------------|------------|----------------------------------|
| `topic`      | str        | `OCS/charging`                   |
| `content`    | str        | `MK201=50MB và MK311=50MB`       |
| `source`     | str        | `conversation` \| `file` \| `whatsapp` |
| `tags`       | list[str]  | `["OCS", "MK201", "Bitel"]`      |
| `confidence` | float      | `0.91`                           |
| `created_at` / `updated_at` | ISO 8601 | `2026-07-05T…Z`        |
| `ref`        | str \| null| session id / file path / thread  |

## API

### REST (`wiki_agent.app:app`, port 8010)

| Method | Path                     | Mục đích                                     |
|--------|--------------------------|----------------------------------------------|
| POST   | `/ingest/conversation`   | Hướng B — extract facts từ transcript        |
| POST   | `/ingest/file`           | Hướng A — index file Markdown (conf=1.0)     |
| POST   | `/ingest/whatsapp`       | Phase 3 — thread WhatsApp → classify → extract → store |
| GET    | `/wiki/search`           | semantic search (`q`, `topic?`, `source?`, `limit`) |
| GET    | `/wiki/topics`           | danh sách topic + count + sources            |
| GET    | `/health`                | liveness                                     |

Mọi endpoint (trừ health) cần `Authorization: Bearer $WIKI_AUTH_TOKEN`.

### MCP tools (`wiki_agent.mcp_server:app`, port 8011)

- `search_wiki(query, topic?, source?, limit=5)`
- `list_wiki_topics()`

Streamable HTTP transport (MCP 2025-03-26), cùng shape với `mcp-http-server` của
agentMem0 nên đặt được sau chung Caddy/OAuth.

## Chạy thử nhanh

```bash
cp .env.example .env        # điền OPENAI_API_KEY, ANTHROPIC_API_KEY, tokens
docker compose up --build   # dựng qdrant + wiki-api + wiki-mcp
```

Ingest một hội thoại:

```bash
curl -s localhost:8010/ingest/conversation \
  -H "Authorization: Bearer $WIKI_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transcript":[{"role":"user","content":"MK201 charge 50MB"}],"session_id":"s1"}'
```

Tìm lại:

```bash
curl -s "localhost:8010/wiki/search?q=OCS%20charge&limit=3" \
  -H "Authorization: Bearer $WIKI_AUTH_TOKEN"
```

---

## Hệ sinh thái — 3 dự án con

`wikiAgent` là điểm hội tụ của 3 dự án. Mỗi dự án lo một nguồn knowledge trong
pipeline:

| Dự án | Vai trò trong wikiAgent | Phase | Trạng thái |
|-------|-------------------------|-------|------------|
| [`agentMem0`](https://github.com/vanbienperu3107/agentMem0) `Python` | Memory + knowledge server (MCP+REST), chia sẻ **Qdrant** & keys; gọi `extract_and_store()` sau summarizer | 1 | ✅ Production |
| [`syncthingMem0`](https://github.com/vanbienperu3107/syncthingMem0) `Go` | File sync transport WSS/443 → đẩy Markdown lên Hub → `POST /ingest/file` | 2 | 🔄 Building |
| **WhatsApp agent** `Node.js` | Baileys realtime → Qwen classify → Haiku extract → `POST /ingest/whatsapp` | 3 | 📋 Planned |

> **Cách kết hợp:** ba nguồn knowledge (hội thoại / file / WhatsApp) đi qua ba
> giải pháp ingest riêng, nhưng cùng đổ về một `wiki_knowledge` và query qua
> cùng một MCP/REST. `agentMem0` lo lưu trữ & auth, `syncthingMem0` lo vận
> chuyển file, WhatsApp agent lo realtime chat.

### Tích hợp với agentMem0

Thêm 3 dòng sau summarizer trong `archive-api/app.py`:

```python
from wiki_agent import knowledge_extractor
n_facts = knowledge_extractor.extract_and_store(transcript, session_id=session_id)
```

Trỏ `QDRANT_INTERNAL_URL` về Qdrant dùng chung và bỏ service `qdrant` trong
`docker-compose.yml`. Chi tiết map từng phase với dự án: xem
[`docs/ECOSYSTEM.md`](docs/ECOSYSTEM.md).

---

## Roadmap 5 Phase

Repo này hiện thực **Phase 1**. Endpoint & schema đã định hình sẵn cho các phase sau:

| Phase | Nội dung | Thời gian | Trạng thái (repo này) |
|-------|----------|-----------|------------------------|
| 1 | Wiki Knowledge Layer (conversation → facts) | 6–7/2026 | ✅ đã hiện thực |
| 2 | File Sync (`/ingest/file`) — chờ syncthingMem0 WSS | 7–8/2026 | ✅ endpoint sẵn sàng |
| 3 | WhatsApp pipeline — chờ V2Ray proxy | 8–9/2026 | ✅ server-side (`/ingest/whatsapp`) · chờ Baileys client |
| 4 | RAG 2.0 (hybrid BM25+vector, reranker, time-aware) | 9–10/2026 | 🔒 sau 50 query thực tế |
| 5 | Multi-source consolidation (dedup, contradiction, versioning) | 10–12/2026 | 🔒 nightly job |

### Ước tính chi phí (toàn stack Phase 1–3)

| Item | $/tháng |
|------|---------|
| VPS (Contabo/Hetzner) | 5–10 |
| Neon Postgres / Cloudflare R2 | 0–1 |
| OpenAI Embeddings | 1–3 |
| Claude Haiku (extractor) | 2–5 |
| Qwen 7B DeepInfra (WhatsApp classify) | 1–2 |
| **Tổng** | **~$10–21/tháng** |

Kế hoạch đầy đủ (5 tab tương tác): [`docs/ROADMAP-3.0.html`](docs/ROADMAP-3.0.html).

## Phát triển

```bash
pip install -r requirements-dev.txt
pytest -q
```

Test mock LLM, embeddings và Qdrant nên chạy offline. CI ở
`.github/workflows/ci.yml`.

## License

Apache-2.0 — xem [LICENSE](LICENSE). Một phần của Personal AI Knowledge System.
