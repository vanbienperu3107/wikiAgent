# Ecosystem — cách wikiAgent kết hợp 3 dự án con

`wikiAgent` không chạy một mình. Nó là **lớp knowledge** ngồi giữa 3 dự án con
của Personal AI Knowledge System. Tài liệu này map từng dự án vào một điểm tích
hợp cụ thể theo từng phase.

```
   AI conversation ───► agentMem0 (archive-api)
                          summarizer.py → knowledge_extractor.py ──┐
                                                                    │
   Markdown file ───► syncthingMem0 (WSS/443) ──► Hub webhook ──────┤
                                                                    │
   WhatsApp chat ───► WhatsApp agent (Baileys) ──► POST /ingest/ ───┤
                          (qua V2Ray proxy)                         │
                                                                    ▼
                                                          wiki_knowledge (Qdrant dùng chung)
                                                                    │
                                                 search_wiki · list_wiki_topics
                                                     (MCP + REST, sau chung Caddy/OAuth)
```

## 1. agentMem0 — lõi lưu trữ & auth (Phase 1)

**Repo:** https://github.com/vanbienperu3107/agentMem0 · Python · ✅ Production

- **Chia sẻ hạ tầng:** wikiAgent dùng lại đúng Qdrant instance, OpenAI &
  Anthropic keys, và có thể đặt sau chung Caddy 2.8 + sslh/OAuth 2.1.
- **Điểm gọi:** trong `archive-api/app.py`, ngay sau khi summarizer chạy:

  ```python
  from wiki_agent import knowledge_extractor
  n_facts = knowledge_extractor.extract_and_store(transcript, session_id=session_id)
  ```

- **Backward compatible:** 8 MCP tool cũ và collection `mem0_mcp_selfhosted`
  không đổi. wikiAgent chỉ thêm collection `wiki_knowledge` và 2 tool mới.

## 2. syncthingMem0 — transport file sync (Phase 2)

**Repo:** https://github.com/vanbienperu3107/syncthingMem0 · Go · 🔄 Building

- **Vai trò:** đồng bộ Markdown từ máy bất kỳ (VS Code…) lên VPS Hub qua
  WSS/443, phù hợp môi trường proxy-heavy.
- **Điểm gắn:** sau khi Hub reconcile ghi file `.md` thành công → Go webhook gọi
  `POST /ingest/file` của wikiAgent với `{path, content}`.
- wikiAgent dùng `uuid5(path)` để mỗi lần re-sync là ghi đè, `confidence=1.0`
  (bạn viết runbook thủ công ⇒ tin cậy tuyệt đối).
- **Chờ:** syncthingMem0 hoàn tất WSS transport + JWT auth trước khi bật Phase 2.

## 3. WhatsApp agent — pipeline realtime (Phase 3)

**Repo:** *chưa tạo* · Node.js · 📋 Planned

- Baileys v7+ → buffer 5 phút theo thread → Qwen 7B (DeepInfra) classify
  keep=true/false → Claude Haiku extract → privacy filter → `POST /ingest/whatsapp`.
- wikiAgent đã reserve `source: "whatsapp"` và dùng sha256(content) để dedup.
- **Chờ:** V2Ray over WebSocket + Nginx proxy để Baileys kết nối WhatsApp Web.

## Bảng tổng hợp

| Nguồn knowledge | Giải pháp ingest | Transport | Đích |
|-----------------|------------------|-----------|------|
| AI conversation | `knowledge_extractor.py` (agentMem0) | HTTPS nội bộ | `wiki_knowledge` |
| Markdown file | `POST /ingest/file` | syncthingMem0 WSS/443 | `wiki_knowledge` |
| WhatsApp | `POST /ingest/whatsapp` | Baileys + V2Ray | `wiki_knowledge` |

Tất cả cùng đổ về một collection, cùng query qua `search_wiki` / `list_wiki_topics`.
