# Database package reference

This document provides an overview of the Neo4j-backed data access layer in [database](../../database).

---

## Table of Contents

1. [Overview](#overview)
2. [Connection](#connection)
3. [Database facade](#database-facade)
4. [Annotation subpackage](#annotation-subpackage)
5. [Supporting modules](#supporting-modules)

---

## Overview

The **Database** class in [database/database.py](../../database/database.py) holds an async Neo4j driver and exposes domain accessors such as `db.person`, `db.text`, and `db.edition`. Their async methods perform Cypher queries and return Pydantic models, dictionaries, or IDs. Graph labels and relationships are documented in [neo4j_schema.yaml](../../database/neo4j_schema.yaml).

---

## Connection

**Constructor:** `Database(neo4j_uri: str, neo4j_auth: tuple[str, str], neo4j_database: str = "neo4j")`

The caller supplies connection settings. Construction creates the driver; connectivity is checked separately.

**Methods:**

- `await verify_connectivity()` – Checks the connection.
- `get_session() -> AsyncSession` – Returns a session for the configured database; use it with `async with`.
- `await close()` – Closes the driver.
- `async with Database(uri, (username, password)) as db` – Checks connectivity on entry and closes the driver on exit.

---

## Database facade

Each attribute below provides operations for its domain.

| Attribute | Module | Responsibility |
|-----------|--------|-----------------|
| **db.api_key** | [api_key_database.py](../../database/api_key_database.py) | API key creation, validation, and optional binding to an application. |
| **db.application** | [application_database.py](../../database/application_database.py) | Applications that own categories and tags; texts and editions are shared. |
| **db.text** | [text_database.py](../../database/text_database.py) | Text/work CRUD, contributions, commentary/translation relationships, and filtered listing. |
| **db.edition** | [edition_database.py](../../database/edition_database.py) | Edition CRUD, content, related editions, and text relationships. |
| **db.annotation** | [database.py](../../database/database.py) (AnnotationDatabase) | Access to the annotation modules listed below. |
| **db.alignment** | [alignment_database.py](../../database/alignment_database.py) | Direct edition-pair segment alignment relationships. |
| **db.segment** | [segment_database.py](../../database/segment_database.py) | Segment retrieval, related segments, tag context, and span lookup; segments are created through segmentation. |
| **db.recording** | [recording_database.py](../../database/recording_database.py) | Recording metadata and edition associations. |
| **db.person** | [person_database.py](../../database/person_database.py) | Person CRUD and listing with BDRC/wiki filters; name search uses the catalog search service. |
| **db.language** | [language_database.py](../../database/language_database.py) | Language code creation, deletion, and listing. |
| **db.category** | [category_database.py](../../database/category_database.py) | Application-owned categories and parent/child hierarchy. |
| **db.tag** | [tag_database.py](../../database/tag_database.py) | Application-owned tags and their attachment to texts and segments. |
| **db.span** | [span_database.py](../../database/span_database.py) | Span adjustments within the content service's validated edit transaction. |

---

## Annotation subpackage

[database/annotation/](../../database/annotation/) contains the modules attached under **db.annotation**:

| Attribute | File | Responsibility |
|-----------|------|-----------------|
| **db.annotation.segmentation** | [segmentation_database.py](../../database/annotation/segmentation_database.py) | Segmentation annotations and their segments. |
| **db.annotation.pagination** | [pagination_database.py](../../database/annotation/pagination_database.py) | Pagination (volume/page) annotations. |
| **db.annotation.table_of_contents** | [table_of_contents_database.py](../../database/annotation/table_of_contents_database.py) | Table of contents annotations with localized section titles, summaries, spans, and recursive subsection hierarchy. |
| **db.annotation.note** | [note_database.py](../../database/annotation/note_database.py) | Note annotations (currently durchen). |
| **db.annotation.mark** | [mark_database.py](../../database/annotation/mark_database.py) | Span-only mark annotations (currently yigchung). |
| **db.annotation.bibliographic** | [bibliographic_database.py](../../database/annotation/bibliographic_database.py) | Bibliographic metadata annotations. |

---

## Supporting modules

These modules are not exposed as `db.<name>` but are used internally by the facade modules.

| Module | Responsibility |
|--------|----------------|
| [database_validator.py](../../database/database_validator.py) | Transaction-level validation of text creation, referenced entities, languages, tags, and edition spans. |
| [nomen_database.py](../../database/nomen_database.py) | Localized names/titles and alternative-name links. |
| [contribution_database.py](../../database/contribution_database.py) | Shared contribution creation and updates. |
| [content_state.py](../../database/content_state.py) | Internal edition revision guards and validated reads. |
| [annotation/single_span.py](../../database/annotation/single_span.py) | Shared persistence for bibliographic metadata, notes, and marks. |

For method-level detail and Cypher usage, see the linked source files.

### Manual schema upgrades

Run `python -m scripts.migrate` for a read-only audit of the configured
`NEO4J_DATABASE`. With application writes paused, run
`python -m scripts.migrate --apply`. Run only one migration command at a time.
Each invocation repeats the idempotent steps, so interrupted runs can be retried.
Apply never guesses repairs for malformed
annotations or duplicate titles: resolve audit failures first, from source data
where necessary.

The command rejects empty pagination structures, installs retained-entity
constraints and lookup indexes, and updates APOC
triggers. Existing metadata discarded by old writers cannot be recovered by
this migration. Allow the configured APOC trigger refresh interval to elapse
and verify the audit before resuming writes.

Title-language uniqueness uses the existing APOC trigger and title/language
relationships. The trigger locks the affected Language nodes in code order before
checking duplicates, serializing title checks that share a language. No duplicate
language property or additional node label is stored.

Content backfill records existing S3 keys and fills missing lengths and revisions without a separate migration marker or copying files. For existing deployments, use `python -m scripts.migrate --content` to audit actual objects and `--apply --content` during the write pause. Search reindexing is an independent manual command. See the [maintenance runbook](../design/cutover.md). Internal revisions do not add requirements to editor requests.
