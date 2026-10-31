# Content state and search: manual maintenance

Ordinary deployment updates and restarts the API. It does not run migrations or reindexing. Complete any required data upgrade before starting a release that depends on it. Content-edit request and response bodies stay unchanged; edition revisions remain internal.

## What needs migration

| Data | Change and reason |
| --- | --- |
| Neo4j schema | Install constraints, indexes, enum values, and corrected triggers. Title-language uniqueness uses the existing title/language relationships, with temporary locks on Language nodes during the duplicate check. No title-data backfill is needed. |
| Pagination | Audit for empty pages, volumes, and paginations. Correct invalid records before upgrading. Nonempty pages are ordered by offsets; no ordering field or backfill is needed. |
| Segmentation | Audit line continuity within and between segments. Gaps and overlaps require an explicit correction before upgrade; migration does not guess new boundaries. |
| Editions | Record each existing S3 key, verified content length, and internal revision. Existing files stay at their current locations; no copy is needed. Future edits always write fresh keys. |
| Search | Manually rebuild indexes when changing mappings or repairing missed updates. Content projection version 3 changes the chunk mapping and adds immutable-content references. Keep the names `content-search` and `catalog-search`. |

Schema upgrades repeat idempotent steps without a version marker. Content backfill sets missing fields; reindexing reads current canonical records. Application ownership rules do not require rewriting valid Work relationships.

Recordings need no migration. Audio paths remain derived from edition ID, recording ID, and format.

## Prepare

1. Back up Neo4j and retain existing S3 objects and search indexes. Rehearse with representative data, including missing content, invalid spans, legacy empty pages, and recordings.
2. Prepare the intended release checkout with Python 3.14, its dependencies, and target-environment configuration. Set `NEO4J_DATABASE` explicitly when it is not `neo4j`.
3. If search is used, install the required ICU, Tibetan, and BDRC OpenSearch plugins. Keep `OPENSEARCH_INDEX=content-search` and `OPENSEARCH_CATALOG_INDEX=catalog-search`. Startup accepts indexes or aliases with the required projection version; incompatible mappings need a rebuild, not a different configured name.
4. Run `python -m scripts.migrate --content` for a read-only audit. Correct reported data problems before applying changes.

## Upgrade data

Stop all API/import writers and drain in-flight requests. Run only one maintenance command at a time. Measure the required pause during rehearsal; it is not established by local tests. Old mutable-content writers must remain stopped after cutover.

```bash
python -m scripts.migrate --apply --content
```

The content backfill verifies actual S3 lengths and annotation bounds, then fills missing pointers and revisions. It never uploads, copies, or deletes S3 objects. After an interruption, resolve the failure and rerun the command before starting the API.

Search rebuilding is separate and optional for core CRUD. With API/import writers still stopped, rebuild when needed:

```bash
python -m scripts.reindex --writes-paused
```

The command builds fresh indexes from Neo4j/S3, checks document counts, and switches both configured names together. When a name already refers to an alias, its previous backing indexes remain available. On the first rebuild of concrete indexes, the final atomic switch deletes those concrete indexes and replaces them with aliases named exactly `content-search` and `catalog-search`. Take a snapshot before this first conversion if the old search data must be retained. The names in application configuration stay unchanged. Each explicit invocation rebuilds; no search-version marker skips repair work.

Start the API and check the liveness endpoint, `GET /__/health`.

If search initialization fails, the API starts with search disabled. Repair its configuration or connection and restart the API to enable it.

## Routine operation

Search updates run as best-effort background callbacks after API mutations. Failures are logged and can leave missing or stale results until a later update or manual reindex. There is no separate worker service, persistent job queue, or upload journal. Direct database imports should be followed by a manual reindex when search freshness is needed.

Uploads use fresh S3 keys. Failed uploads and objects no longer referenced by database records are retained. Deleting a recording or edition removes its database data and leaves stored files. The application does not require S3 object/version deletion permissions.

## Rollback

Use a previous application version compatible with immutable content pointers. Returning to an older mutable-path writer requires a coordinated restoration or reverse migration; a code-only rollback is insufficient once new edits exist. The first concrete-index conversion needs its snapshot to recover the old search indexes; later alias rebuilds retain their previous backing indexes. If rolling search aliases back after further content changes, rebuild first when current results are required.

Production-data rehearsal, migration, and deployment have not been executed from this workspace.
