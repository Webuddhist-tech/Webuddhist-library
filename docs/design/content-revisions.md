# Content and optional search design

Implemented decisions for the remediation plan. Activation in an existing environment
requires the [write-pause migration and cutover](cutover.md).

## Search representation

Use 4,000-character chunks with a 500-character overlap. Each stores its edition,
text, immutable content-object key, starting offset, and original Unicode text.
There are no copied titles, sources, languages, or segment arrays. Content uses a
`wildcard` field for literal candidates and an analyzed subfield for relevance ranking.
Exact search uses analyzed phrase scores without requiring an analyzed match;
literal verification remains mandatory. Both modes sort by relevance, then edition
ID and chunk offset for ties.

Exact candidate filtering uses up to 64 characters, within the chunk overlap.
Longer repeated prefixes exceeded OpenSearch's wildcard automaton limit in integration
tests. The full literal is still verified after retrieving candidates, and every
match starting in a chunk has its candidate prefix entirely in that chunk.
Exact queries up to the overlap need no adjacent reads. Longer queries fetch at
most three following chunks per hit in one batched request, checking that their
content-object keys match before joining them. A match belongs to the chunk whose
first 3,500 characters contain its start; overlap therefore needs no exact-hit
deduplication cache. Similar search ranks passages and suppresses overlapping
contexts, allowing multiple results from an edition.

Segment IDs come from the current graph. The first database query captures edition
revisions before resolving segments; a second query rejects results changed during
that read. Deleted, outdated, and unsegmented hits are excluded. Search processes at
most 1,000 candidate chunks and 10,000 candidate occurrences, returning at most the
requested limit. Full editions are loaded only for indexing, not while searching.

Content projection version 3 requires an explicit index rebuild. No background
queue, publication tracking, or automatic deployment reindexing is added.

The previous whole-edition experiment measured indexing costs:

| Characters | Representation | Documents | JSON bytes | Build seconds | Index seconds |
| --- | --- | --- | --- | --- | --- |
| 100,000 | Existing chunks | 29 | 279,551 | 0.065 | 0.597 |
| 100,000 | Whole edition | 1 | 178,982 | negligible | 0.089 |
| 1,000,000 | Existing chunks | 286 | 2,818,624 | 5.310 | 0.750 |
| 1,000,000 | Whole edition | 1 | 1,776,954 | negligible | 0.237 |

These historical, local measurements used synthetic mixed Tibetan/English/Chinese
content with an enclosing segment plus dense short segments, on OpenSearch 3.5.0.
They do not measure current request latency or relevance. Whole-edition retrieval
was replaced because it transferred and scanned full texts during searches. The
old indexing-only benchmark and frozen legacy chunk builder have been removed.
The current chunk implementation passed its integration tests against OpenSearch
3.5.0; see the [validation results](remediation-results.md). No production search
latency benchmark has been run.

## Best-effort search updates

API mutations schedule ordinary background callbacks after their database transaction. Each callback reads current data and updates or deletes the corresponding search documents. Work category/tag changes refresh related texts, including translations. Errors are logged without failing the successful mutation.

There is no persistent queue, separate worker, publication-version counter, or stored deletion marker. Updates may be lost during a crash or arrive out of order. Content search rejects hits whose object key no longer matches the edition; catalog search hydrates current records and skips deleted ones. Search freshness is optional. Run the manual reindex command to repair missed updates or after direct database imports.

## Content writes

Upload to a fresh S3 key, then commit the content pointer, length, span changes, and internal revision in one database transaction. Generate new entity IDs before the transaction so database retries reuse them; edits check the revision captured before upload so their offsets cannot be applied twice by a database retry. S3 I/O stays outside database transactions.

No upload record, request fingerprint, saved outcome, expiry sweep, or object cleanup is needed. A storage or database failure can leave an unused S3 object. Independent HTTP requests are separate operations.

Failure boundaries:

- An upload failure stops before any database mutation. Existing objects are never overwritten.
- Edition creation, content edits, annotation/alignment changes, recording creation,
  and edition deletion each keep their graph changes in one transaction. Any error
  before commit rolls back all those changes, including internal revisions.
- Text, person, and recording updates build and validate their returned model inside
  the write transaction, so a response-read failure also rolls back the changes.
- Search runs after commit and is best-effort; a search failure does not undo the write.

No compensating S3 overwrite is needed: an aborted transaction retains the previous
content pointer. A disconnect during commit can leave the outcome unknown to the
caller, but the database still commits the whole transaction or none of it. A failed
HTTP response therefore does not always mean the operation was rolled back, and
there is no durable replay mechanism to reconstruct a lost response.

Content edits must leave at least one character. Client revision fields are not required. The server rejects conflicting changes during preparation, but cannot detect otherwise valid offsets from an older editor view. Annotation changes also advance the internal revision, even when text bytes remain unchanged.

Annotation writes acquire Neo4j's native write lock by incrementing the revision
before validation. A failed transaction rolls back the increment too. Content edits
and segmentation removal explicitly lock connected editions before reading their
mutable state; alignment writes increment revisions in edition-ID order. Operations
on Work tags lock Works before Tags; deletion rechecks attachments after locking.

Uploads use S3 `If-None-Match: *` to avoid replacing an existing key. Previous content, abandoned uploads, and deleted recordings' audio are retained. Initial migration records existing object paths rather than copying files. See the [manual maintenance runbook](cutover.md).

## Alignment traversal

Traversal retains its depth limit, visited set, and consistency checks. The arbitrary
segment/connection caps and connection-counting query were removed.
