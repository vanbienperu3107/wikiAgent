# Phase 1 — Wiki Knowledge Layer · Kế hoạch triển khai

> Hướng B: **AI conversation → extract structured facts → `wiki_knowledge` →
> `search_wiki` / `list_wiki_topics`**.
> Tiêu chí nghiệm thu (roadmap): `search_wiki("OCS error 411")` trả về fact từ
> một session trước đó.

## 0. Tình trạng — code đã xong, còn phần tích hợp

5 task trong checklist roadmap ↔ trạng thái thực tế trong repo `wikiAgent`:

| # | Task roadmap | Trạng thái |
|---|--------------|-----------|
| 1 | `knowledge_extractor.py` — Haiku extract facts (topic/content/tags/confidence) | ✅ Xong (có privacy filter + fallback OpenAI + test) |
| 2 | `WIKI_COLLECTION` + `ensure_wiki_collection()` (1536, Cosine) | ✅ Xong (`qdrant_helper.py`, +index `topic`/`source`) |
| 3 | Gọi extractor sau summarizer trong `archive-api/app.py` | ⬜ **Chưa** — cần sửa agentMem0 |
| 4 | MCP tool `search_wiki(query, topic?, limit)` | ✅ Logic xong (`wiki_search.py`) · ⬜ chưa gắn vào mcp-http-server |
| 5 | MCP tool `list_wiki_topics()` | ✅ Logic xong · ⬜ chưa gắn |

➡️ **Phần còn lại của Phase 1 = wiring `wiki_agent` vào agentMem0 production +
deploy + verify.** Thư viện đã build & test (16/16 pass) trong repo `wikiAgent`.

## 1. Quyết định kiến trúc (cần chốt trước khi code)

**Option A — Library import (khuyến nghị, đúng roadmap).**
agentMem0 cài `wiki_agent` như một package và gọi in-process. Đúng tinh thần
"3 dòng thêm" và "không thêm service mới"; dùng lại Qdrant + container sẵn có.

**Option B — Microservice.**
Chạy `wiki-api` / `wiki-mcp` riêng, agentMem0 gọi qua HTTP. Tách bạch hơn nhưng
thêm 1–2 service (trái nguyên tắc "no new service" của roadmap) + thêm network hop.

> Kế hoạch dưới đây viết theo **Option A**.

## 2. Milestones

### M1 — Nối dependency (agentMem0 ⇐ wiki_agent)
- Thêm `wiki_agent` vào agentMem0 qua **git submodule** `libs/wikiAgent`
  *hoặc* `pip install "git+https://github.com/vanbienperu3107/wikiAgent@v0.1.0"`
  trong `archive-api/requirements.txt` và `mcp-http-server/requirements.txt`.
- Đảm bảo env trong 2 container:
  - Có sẵn: `OPENAI_API_KEY`, `QDRANT_INTERNAL_URL`.
  - Thêm: `ANTHROPIC_API_KEY` (extractor Haiku), `WIKI_COLLECTION=wiki_knowledge`,
    `WIKI_MIN_CONFIDENCE=0.5`.

### M2 — Ingestion hook (`archive-api/app.py`, ~3 dòng)
Trong `summarize_session`, ngay sau khối embedding upsert:
```python
try:
    from wiki_agent import knowledge_extractor
    n_facts = knowledge_extractor.extract_and_store(transcript, session_id=session_id)
except Exception as e:
    print(f"wiki extract failed (non-fatal): {e}")
    n_facts = 0
return {"id": session_id, "summary": summary_text, "wiki_facts": n_facts}
```
Non-fatal như khối embedding hiện có — không được làm hỏng luồng summarize.

### M3 — Query tools (`mcp-http-server/app.py`)
- `from wiki_agent import wiki_search`.
- Thêm 2 entry vào `TOOLS`: `search_wiki`, `list_wiki_topics` (copy schema từ
  `wiki_agent/mcp_server.py`).
- Xử lý trong `exec_tool` — gọi in-process, không cần REST hop:
  ```python
  if name == "search_wiki":
      return wiki_search.search_wiki(args["query"], topic=args.get("topic"),
                                     source=args.get("source"), limit=args.get("limit", 5))
  if name == "list_wiki_topics":
      return wiki_search.list_wiki_topics()
  ```
- (Tùy chọn) thêm `search_wiki` vào `memory-rest-api` + OpenAPI cho ChatGPT.

### M4 — Collection & backfill
- `ensure_wiki_collection()` tự chạy ở lần ingest đầu (đã có trong `store_facts`).
- (Tùy chọn) script backfill: extract lại N session cũ để có sẵn data cho test
  nghiệm thu.

### M5 — Test & CI
- Unit test đã có trong wikiAgent (offline, mock LLM/Qdrant).
- Thêm smoke test vào CI agentMem0: sau deploy, `tools/list` có `search_wiki`;
  gọi `search_wiki` trả 200. Ghép vào bộ 6 smoke test hiện tại (~4 phút).

### M6 — Nghiệm thu (roadmap acceptance)
1. Chạy 1 hội thoại chứa fact kỹ thuật (vd OCS/charging).
2. `POST /sessions/{id}/summarize` → kiểm tra `wiki_facts > 0`.
3. `search_wiki("OCS error 411")` → phải trả về fact vừa lưu.
4. `list_wiki_topics()` → thấy topic `OCS/...` với count ≥ 1.

## 3. Rủi ro / lưu ý
- **Latency:** extraction đồng bộ trong summarize thêm ~1–3s/session. Chấp nhận
  được ở Phase 1; có thể chuyển async (background task) sau nếu cần.
- **Auth key:** cần `ANTHROPIC_API_KEY` trong container archive-api. Nếu chỉ có
  OAT Max ở client thì extractor tự fallback sang OpenAI (`gpt-4o-mini`).
- **Privacy:** message chứa keyword nhạy cảm bị chặn trước khi tới LLM (đã có).
- **Backward compat:** 8 tool cũ + collection `mem0_mcp_selfhosted` không đổi.
- **Cost:** ~$2–5/tháng Haiku cho ~1000 conversations (theo roadmap).

## 4. Thứ tự & ước lượng

| Bước | Việc | Ước lượng |
|------|------|-----------|
| M1 | Dependency + env | ~30' |
| M2 | Ingestion hook | ~20' |
| M3 | 2 MCP tool | ~30' |
| M4 | Collection/backfill | ~20' |
| M5 | Smoke test + CI | ~30' |
| M6 | Verify nghiệm thu | ~20' |

Tổng ~2.5h · gói trong **1 PR vào agentMem0** (branch `claude/create-wikagent-repo-kqi4ys`).

## 5. Định nghĩa "Done" cho Phase 1
- [ ] agentMem0 import được `wiki_agent`, env đầy đủ.
- [ ] Mỗi session summarize xong tự sinh facts vào `wiki_knowledge`.
- [ ] `search_wiki` + `list_wiki_topics` xuất hiện trong MCP `tools/list`.
- [ ] Smoke test xanh trong CI.
- [ ] Nghiệm thu M6 pass trên production `claude.hangocthanh.io.vn`.
