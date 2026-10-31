# Remediation implementation

Existing editing-client request and response bodies are preserved. Edition revisions are internal and protect content/annotation consistency during concurrent requests.

## Current implementation

| Area | Result |
| --- | --- |
| Ownership | Bound API keys mutate only their application's tags/categories. Shared Work changes serialize across translations. Translations inherit category and reject a submitted category. |
| Annotations | Metadata round-trips, zero-width markers remain visible, segmentation is contiguous without overlaps/gaps, and replacement text belongs to one segment/page. Pages must contain text; edits that empty a page are rejected. Page order uses offsets without a stored ordinal. TOC and imported Attribute deletion is complete. |
| Schema | Title-language uniqueness through the existing trigger with Language-node locks, update-aware triggers, TOC hierarchy enforcement, and explicit, repeatable audited upgrades. No migration tracking or duplicated title-language property. |
| Content | Upload to a fresh S3 key, then commit the key, spans, length, and revision in one database transaction. Conflicting concurrent edits and edits removing all content are rejected. |
| Audio | Upload the spooled file before publishing metadata, without an extra full-file buffer. Derive its storage key from edition ID, recording ID, and format; no audio-key property or migration is needed. |
| Storage retention | Failed uploads and unreferenced objects remain in S3. Database deletion does not delete stored content/audio. |
| Search | Small overlapping content chunks, literal Unicode matching across boundaries, current graph segment joins, bounded search work, and canonical catalog hydration. HTTP mutations request best-effort background updates. |
| Deployment | Update and restart only the API; check liveness. Migration and reindexing are explicit maintenance commands. |

Removed upload tracking, durable search jobs, worker status, the worker process/service, publication versions, stored deletion markers, automatic object cleanup, and their dedicated tests. Content migration now reuses existing S3 files rather than copying them. Manual reindexing reads canonical records directly.

Internal edition guards and transactional span changes remain because they protect user content. Search failures do not fail successful writes; updates can be missed or reordered and manual reindexing repairs the index. There is no durable request replay or exactly-once HTTP guarantee. Valid offsets from an older client view cannot be distinguished from intentional offsets without client state.

Enum nodes and existing bounded alignment traversal remain. Converting either would add a separate migration or consistency problem without serving this simplification.

## Verification and rollout

Validated locally on 2026-09-26 with Python 3.14.2, disposable Neo4j 2026.07.1,
and OpenSearch 3.5.0 with the required analysis plugins:

- `python -m pytest tests/ -m ''`: 902 passed before the optional alignment benchmark
  was removed (901 remaining tests); 50 third-party deprecation warnings from
  testcontainers and aiohttp.
- Ruff lint/format checks, Pyright, Ty, high-confidence Vulture checks, dependency
  consistency, YAML parsing, shell syntax, and Git whitespace checks passed.

Validation caught an OpenSearch automaton-limit failure for long repeated literal
prefixes. Exact search now uses a short candidate prefix and still verifies the full
query. A separate similar-search fixture needed a word boundary before its second
phrase. Lint/type findings were also corrected.

Historical indexing measurements are retained in the [design notes](content-revisions.md); they do not establish production query latency. Follow the [maintenance runbook](cutover.md) for an eventual rollout; production-data rehearsal and deployment remain outstanding.
