# Content Search API Documentation

Content search finds where a query appears in stored edition content and returns the text, edition, and segment locations.

Neo4j identifies the canonical immutable content object in S3. OpenSearch stores 4,000-character chunks with 500 characters of overlap; segment IDs come from the current graph.

## Authentication

Use `X-API-Key` in deployed environments.

```text
X-API-Key: your_api_key
```

## Search Content

```http
GET /v2/content-search
```

Query parameters:

| Query | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | Yes | - | Search string |
| `search_type` | `exact` or `similar` | No | `exact` | Exact substring search or OpenSearch-ranked similar search |
| `limit` | integer | No | `10` | Maximum results, 1-100 |
| `text_id` | string | No | - | Filter to one text |
| `edition_id` | string | No | - | Filter to one edition |

Exact search preserves case, punctuation, and the original Unicode characters without normalization. It escapes wildcard metacharacters, selects candidate chunks with a bounded literal prefix, and verifies the complete substring. Queries can contain 1–10,000 characters; queries longer than the overlap fetch adjacent chunks from the same content object to verify boundary-spanning matches. Each match is returned once. The limit counts occurrences, ordered by chunk relevance, with edition ID and position breaking ties. Analyzed phrase matching supplies optional relevance scores; literal matches remain eligible when the analyzer cannot score them. Similar search ranks near-phrase matches within chunks and returns non-overlapping contexts, with `match_span: null`. It can return multiple passages from one edition.

Deleted, stale, unsegmented, and non-overlapping passages are filtered against Neo4j in batches, with an internal revision recheck. Search stops when it fills the requested limit or inspects 1,000 candidate chunks or 10,000 candidate occurrences. Results may therefore be incomplete, and indexing updates may lag behind content changes.

`context` is a plain text fragment from the matched passage. `context_span` is the location of that returned `context` in the full edition text.

`segment_ids` contains all segment IDs covered by the result. For exact search, these are the segments overlapping `match_span`; for similar search, these are the segments covered by `context_span`.

Response:

```json
[
  {
    "text_id": "TXT123",
    "edition_id": "ED123",
    "segment_ids": ["SEG001", "SEG002"],
    "context_span": {"start": 80, "end": 220},
    "match_span": {"start": 132, "end": 151},
    "score": 12.4,
    "context": "..."
  }
]
```

Example:

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/content-search?query=%E0%BD%96%E0%BD%91%E0%BD%BA%E0%BC%8B%E0%BD%A3%E0%BD%BA%E0%BD%82%E0%BD%A6&search_type=exact" \
  -H "X-API-Key: your_api_key"
```

Error responses:

- `400 Bad Request`: OpenSearch auth mode is invalid.
- `401 Unauthorized`: Missing or invalid API key in deployed environments.
- `422 Unprocessable Entity`: Missing/oversized `query`, invalid `search_type`, or invalid `limit`.
- `503 Service Unavailable`: Search is unavailable; canonical CRUD remains available.

## Operations

Content mutations and segmentation changes request best-effort background updates after committing. Updates replace an edition's chunks. Errors are logged; partial, missed, or reordered updates can leave missing results until reindexing. Current graph checks exclude deleted editions and hits for old content objects.

For a full rebuild, stop API/import writers, then run:

```bash
python -m scripts.reindex --writes-paused
```

This builds replacements, verifies document counts, and switches both configured names together. Keep `OPENSEARCH_INDEX=content-search` and `OPENSEARCH_CATALOG_INDEX=catalog-search`. The first rebuild replaces concrete indexes with aliases of the same names; subsequent rebuilds retain previous backing indexes. Content projection version 3 requires this explicit rebuild for older chunk or whole-edition mappings; deployment does not reindex automatically. See [the cutover runbook](../design/cutover.md) for backup prerequisites and rollback limits.

## Developer Notes

Relevant code:

- `routers/content_search.py`
- `content_search/service.py`
- `models/content_search.py`
- `models/requests.py`
- `database/segment_database.py`
- `scripts/reindex.py`
