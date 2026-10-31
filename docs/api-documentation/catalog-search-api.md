# Catalog Search API Documentation

Catalog search powers name and title lookups for persons and texts. It backs the `name` filter on `GET /v2/persons` and the `title` filter on `GET /v2/texts`, providing lenient, script-aware matching across languages rather than a plain substring match.

Neo4j remains the canonical store for persons and texts. OpenSearch stores a derived catalog document per person and per text with analyzed label fields used only for matching; the API always returns the canonical records resolved from Neo4j.

## Authentication

Use `X-API-Key` in deployed environments.

```text
X-API-Key: your_api_key
```

## Usage

Catalog search is not a standalone endpoint. It is invoked through the existing list endpoints:

```http
GET /v2/persons?name=<query>
GET /v2/texts?title=<query>
```

- The query must be at least 2 characters.
- Results are resolved back to full person/text records and returned in the standard paginated shape.
- Other filters combine with the search: for texts, `language`, `category_id`, `tag_id`, `tag_id_match`, `author_id`, `bdrc`, and `wiki` are applied as filters alongside the title query; for persons, `bdrc` and `wiki` are applied alongside the name query. `tag_id` accepts comma-separated IDs; `tag_id_match` defaults to `all` and can be set to `any`.
- If catalog search is not configured, these two filters return `503 Service Unavailable`. All other list behavior is unaffected.

## Matching Behavior

Each label (primary and alternative) is indexed under multiple analyzers, and a query is matched against all of them. The label's language tag determines which script-specific analyzer is used, in addition to a language-agnostic folded field.

- **Diacritic-insensitive (all scripts)**: ICU folding removes diacritics and case, so `Śāntideva` and `Santideva` match each other.
- **Sanskrit lenient (labels tagged `sa`, `sa-x-iast`, `pi`)**: the BDRC IAST analyzer normalizes loose romanization, so `Shantideva`, `Santideva`, and `Śāntideva` all match a name stored as `Śāntideva`. This is the only path that understands `sh` ↔ `ś`.
- **Tibetan phonetic (labels tagged `bo`)**: Tibetan Unicode is matched by phonetic romanization, so `zhi ba lha` matches `ཞི་བ་ལྷ་`. Wylie/EWTS input is also supported.

Alternative names/titles are indexed and searched the same way as primary labels, so a query that matches an alternative label still returns the record.

> Note: `sh` ↔ `ś` equivalence only works when the label is tagged as a Sanskrit language (`sa`/`sa-x-iast`/`pi`). A Sanskrit name mistakenly tagged as `en` will still match `Śāntideva`/`Santideva` via diacritic folding, but not `Shantideva`. Tag Sanskrit labels with a Sanskrit language code to get full lenient matching.

## Operations

API mutations schedule best-effort background updates. Work category/tag changes and tag deletion refresh affected translations. Failed or reordered updates can leave stale results; manual reindexing repairs them. There is no separate worker or persistent job queue.

Search hydrates current canonical records and skips deleted hits; pagination offsets count valid results. Searches inspect at most 10,000 candidates, so results can be incomplete and `has_more: false` does not guarantee that no further matches exist. Base-language filters include variants; a complete language tag is specific. Matching is case-insensitive for language tags.

Stop API/import writers before rebuilding:

```bash
python -m scripts.reindex --writes-paused
```

Replacement indexes are built and checked before an atomic switch that preserves the configured name, `catalog-search`. The first rebuild replaces a concrete index with an alias of the same name; subsequent rebuilds retain previous backing indexes. See [the cutover runbook](../design/cutover.md) for backup and rollback details.

## Deployment Prerequisite

The index mapping references custom analyzers (`catalog_sanskrit_roman`, `catalog_tibetan`, and related char filters/tokenizers) that come from two OpenSearch plugins:

- `analysis-tibetan` (BDRC)
- `analysis-bdrc` (BDRC)

Both plugins must be installed on the target OpenSearch cluster before the index can be created — on AWS OpenSearch Service they are added as custom packages and associated with the domain. Index creation fails if the plugins are missing.

For local development and tests, point these environment variables at built plugin ZIPs so the test OpenSearch container installs them:

```bash
OPENSEARCH_TIBETAN_PLUGIN_ZIP=/absolute/path/to/analysis-tibetan.zip
OPENSEARCH_BDRC_PLUGIN_ZIP=/absolute/path/to/analysis-bdrc.zip
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSEARCH_ENDPOINT` | OpenSearch endpoint URL (required for catalog search) | - |
| `OPENSEARCH_CATALOG_INDEX` | Catalog index or alias name | `catalog-search` |
| `OPENSEARCH_AUTH_MODE` | `none`, `basic`, or `aws` | `none` |
| `OPENSEARCH_USERNAME` | Username for `basic` auth | - |
| `OPENSEARCH_PASSWORD` | Password for `basic` auth | - |

## Developer Notes

Relevant code:

- `routers/persons.py`, `routers/texts.py`
- `catalog_search/service.py`
- `search_client.py`
- `models/requests.py`
- `scripts/reindex.py`
- `tests/test_catalog_search.py`
