# Integration tests (real Qdrant)

An end-to-end test that exercises the real Qdrant round-trip across
`qdrant_helper`, `rag`, `consolidation`, and `fact_crud`. Only the OpenAI
embedding call is stubbed (with a deterministic local vectorizer), so no
network access or API key is needed — but a live Qdrant is.

## Run it

```bash
# 1. start a throwaway Qdrant
docker compose -f docker-compose.test.yml up -d

# 2. run the suite against it
WIKI_TEST_QDRANT_URL=http://localhost:6333 python3 -m pytest tests/integration -q

# 3. tear down
docker compose -f docker-compose.test.yml down
```

The test creates a fresh `wiki_test_e2e` collection and deletes it on teardown;
it never touches production collections.

## Auto-skip

If no Qdrant is reachable at `WIKI_TEST_QDRANT_URL` (default
`http://localhost:6333`), the whole module is skipped cleanly — so a plain
`python3 -m pytest` on a machine without Qdrant is a no-op, not a failure.
