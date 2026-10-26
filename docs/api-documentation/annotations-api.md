# Annotations API Documentation

Annotations attach structured information to edition content using character spans. They are created under editions and can also be fetched or deleted directly by annotation ID.

## Concepts

- **Span**: A half-open character range where `start` is inclusive and `end` is exclusive.
- **Lines**: Continuous spans inside a segment or page. Adjacent lines must be sorted and touch each other.
- **Segmentation**: A set of logical content segments for an edition.
- **Segment type**: Every segment has a `type`. It defaults to `"paragraph"` when omitted. Allowed values are `"paragraph"`, `"verse"`, `"title"`, `"back_matter"`, `"front_matter"`, and `"top_segment"`.
- **Alignment**: A mapping between segments in an aligned edition and target segments in another edition.
- **Pagination**: A mapping from character spans to page or folio references.
- **Table of contents**: A table-of-contents style hierarchy for an edition. Each section has a character span, localized title, optional localized summary, and optional nested subsections.
- **Bibliographic metadata**: Span-level metadata such as colophon, title, incipit, or author.
- **Durchen note**: A span-level critical apparatus note.
- **Yigchung mark**: A span-only mark with no text or value payload.

## Authentication

Use `X-API-Key` in deployed environments.

```text
X-API-Key: your_api_key
```

## Create and List Annotations on an Edition

Annotations are created and listed by type under an edition. Durchen and Yigchung batch creation return arrays of ID objects; other `POST` endpoints return `{ "id": "..." }`.

| Type | List | Create |
|------|------|--------|
| Segmentation | `GET /v2/editions/{edition_id}/segmentation` | `POST /v2/editions/{edition_id}/segmentation` |
| Pagination | `GET /v2/editions/{edition_id}/pagination` | `POST /v2/editions/{edition_id}/pagination` |
| Table of contents | `GET /v2/editions/{edition_id}/table-of-contents` | `POST /v2/editions/{edition_id}/table-of-contents` |
| Bibliographic metadata | `GET /v2/editions/{edition_id}/bibliographic` | `POST /v2/editions/{edition_id}/bibliographic` |
| Durchen notes | `GET /v2/editions/{edition_id}/durchens` | `POST /v2/editions/{edition_id}/durchens` |
| Yigchung marks | `GET /v2/editions/{edition_id}/yigchungs` | `POST /v2/editions/{edition_id}/yigchungs` |


### Create Segmentation

```json
{
  "segments": [
    {
      "type": "verse",
      "reference": "1.1",
      "lines": [
        {"start": 0, "end": 50}
      ]
    },
    {
      "reference": "1.2",
      "lines": [
        {"start": 50, "end": 100}
      ]
    }
  ]
}
```

Segments must be sorted by their first line's start offset. Lines inside each segment must be continuous.
`type` is optional and defaults to `"paragraph"`.
`reference` is optional for general segmentation use, but required for segments that will be aligned by reference.

### Edition Pair Alignment

Alignment is no longer an annotation type. Use the edition-pair alignment endpoints to create direct `ALIGNED_TO` relationships between existing segments:

```http
GET /v2/editions/{edition_id}/alignments
PUT /v2/editions/{source_edition_id}/alignments/{target_edition_id}
GET /v2/editions/{source_edition_id}/alignments/{target_edition_id}?limit=500&offset=0
DELETE /v2/editions/{source_edition_id}/alignments/{target_edition_id}
```

`PUT` validates every submitted reference first, then replaces all alignments from source edition segments to target edition segments and returns `204 No Content`. If any reference is missing, the whole request fails and existing alignments are unchanged.

```json
{
  "alignments": [
    {
      "source_segment_reference": "1.1",
      "target_segment_reference": "1.1"
    }
  ]
}
```

If one source segment aligns to multiple target segments, repeat the `source_segment_reference` in multiple rows.

`GET /v2/editions/{edition_id}/alignments` returns directional aligned/target edition and text IDs for every direct alignment involving the edition.

### Create Pagination

```json
{
  "volumes": [
    {
      "index": 34,
      "pages": [
        {
          "reference": "1a",
          "lines": [
            {"start": 0, "end": 500}
          ]
        }
      ]
    }
  ],
  "metadata": {}
}
```

`index` is the volume number in the source collection (for example `34` for Degé Kangyur vol. 34). A single-volume pagination may set `index` to `null` or omit it, meaning the text has only ever had one volume. Multi-volume pagination requires an `index` on every volume, and indexes must be unique; they do not need to start at `1` or be continuous.

### Create table of contents

```json
{
  "metadata": {
    "name": "Main sa bcad"
  },
  "sections": [
    {
      "title": {
        "bo": "ལེའུ་དང་པོ།",
        "en": "Chapter 1"
      },
      "summary": {
        "en": "Opening topic"
      },
      "span": {"start": 0, "end": 1200},
      "subsections": [
        {
          "title": {
            "en": "Section 1.1"
          },
          "summary": {
            "en": "Introductory topic"
          },
          "span": {"start": 0, "end": 350},
          "subsections": []
        }
      ]
    }
  ]
}
```

Each table of contents can contain one or more root `sections`. Sections can be nested with `subsections` to represent a table of contents or Tibetan `sa bcad` hierarchy. Each section and subsection has the same shape:

- `title`: localized string, required.
- `summary`: localized string, optional.
- `span`: half-open character range for the section.
- `subsections`: nested child sections, optional and defaults to an empty list.

Each subsection span must be fully contained inside its parent section span.
An edition can have multiple table of contents. Table of contents sections are managed as part of the table of contents annotation; they are not standalone API resources.
Returned sections and subsections are ordered by their span start/end positions.

### Create Bibliographic Metadata

```json
{
  "span": {"start": 5000, "end": 5500},
  "type": "colophon",
  "metadata": {}
}
```

Supported types:

- `colophon`
- `incipit`
- `alt_incipit`
- `alt_title`
- `person`
- `title`
- `author`

### Create Durchen Notes

```json
[
  {
    "span": {"start": 100, "end": 150},
    "text": "Variant reading found in manuscript B",
    "metadata": {}
  },
  {
    "span": {"start": 300, "end": 330},
    "text": "Second variant reading"
  }
]
```

The request must contain at least one item. All notes are created atomically, and the response preserves request order:

```json
[
  {"id": "DUR123"},
  {"id": "DUR124"}
]
```

### Create Yigchung Marks

```json
[
  {
    "span": {"start": 200, "end": 205}
  },
  {
    "span": {"start": 350, "end": 360}
  }
]
```

The request must contain at least one item. All items are created atomically, and the response preserves request order:

```json
[
  {"id": "MRK123"},
  {"id": "MRK124"}
]
```

A yigchung carries only its character span. The endpoint assigns the internal mark type `yigchung`; clients do not submit a `type`, `text`, or `value`.

## Fetch and Delete by ID

### Segmentation

```http
GET /v2/editions/{edition_id}/segmentation
GET /v2/editions/{edition_id}/segmentation/segments?limit=500&offset=0
DELETE /v2/editions/{edition_id}/segmentation
```

`GET /v2/editions/{edition_id}/segmentation` response:

```json
{
  "id": "SGN123",
  "edition_id": "ED123",
  "text_id": "TXT123"
}
```

`GET /v2/editions/{edition_id}/segmentation/segments` query:

| Query | Type | Required | Default |
|-------|------|----------|---------|
| `limit` | integer, 1-500 | No | 500 |
| `offset` | integer, >= 0 | No | 0 |

Segments response:

```json
{
  "items": [
    {
      "id": "SEG123",
      "reference": "1.1",
      "lines": [
        {"start": 0, "end": 50}
      ]
    }
  ],
  "has_more": false,
  "offset": 0,
  "limit": 500
}
```

Deleting a segmentation returns `204 No Content`. Any `ALIGNED_TO` relationships attached to its segments are removed with the segments.

### Alignment

```http
GET /v2/editions/{source_edition_id}/alignments/{target_edition_id}?limit=500&offset=0
```

Query:

| Query | Type | Required | Default |
|-------|------|----------|---------|
| `limit` | integer, 1-500 | No | 500 |
| `offset` | integer, >= 0 | No | 0 |

Segments response:

```json
{
  "items": [
    {
      "source_segment": {
        "id": "SEG_SOURCE",
        "reference": "1.1",
        "segmentation_id": "SGN_SOURCE",
        "edition_id": "ED_SOURCE",
        "text_id": "TXT_SOURCE",
        "lines": [{"start": 0, "end": 55}],
        "tag_ids": null
      },
      "target_segment": {
        "id": "SEG_TARGET",
        "reference": "1.1",
        "segmentation_id": "SGN_TARGET",
        "edition_id": "ED_TARGET",
        "text_id": "TXT_TARGET",
        "lines": [{"start": 0, "end": 60}],
        "tag_ids": null
      }
    }
  ],
  "has_more": false,
  "offset": 0,
  "limit": 500
}
```

`DELETE /v2/editions/{source_edition_id}/alignments/{target_edition_id}` deletes the direct relationships only; it does not delete segmentations or segments.

### Pagination

```http
GET /v2/paginations/{pagination_id}
DELETE /v2/paginations/{pagination_id}
```

Response:

```json
{
  "id": "PAG123",
  "edition_id": "ED123",
  "text_id": "TXT123",
  "volumes": [
    {
      "index": 34,
      "pages": [
        {
          "reference": "1a",
          "lines": [
            {"start": 0, "end": 500}
          ]
        }
      ]
    }
  ],
  "metadata": {}
}
```

### Table of contents

```http
GET /v2/table-of-contents/{toc_id}
DELETE /v2/table-of-contents/{toc_id}
```

Response:

```json
{
  "id": "OUT123",
  "edition_id": "ED123",
  "text_id": "TXT123",
  "metadata": {
    "name": "Main sa bcad"
  },
  "sections": [
    {
      "id": "SEC_ROOT",
      "title": {
        "bo": "ལེའུ་དང་པོ།",
        "en": "Chapter 1"
      },
      "summary": {
        "en": "Opening topic"
      },
      "span": {"start": 0, "end": 1200},
      "subsections": [
        {
          "id": "SEC_CHILD",
          "title": {
            "en": "Section 1.1"
          },
          "summary": {
            "en": "Introductory topic"
          },
          "span": {"start": 0, "end": 350},
          "subsections": []
        }
      ]
    }
  ]
}
```

Deleting an table of contents deletes its table of contents sections, section spans, title/summary localized text subgraphs, and metadata.

### Bibliographic Metadata

```http
GET /v2/bibliographic/{bibliographic_id}
DELETE /v2/bibliographic/{bibliographic_id}
```

Response:

```json
{
  "id": "BIB123",
  "edition_id": "ED123",
  "text_id": "TXT123",
  "span": {"start": 5000, "end": 5500},
  "type": "colophon",
  "metadata": {}
}
```

### Durchen Note

```http
GET /v2/durchens/{durchen_id}
DELETE /v2/durchens/{durchen_id}
```

Response:

```json
{
  "id": "DUR123",
  "edition_id": "ED123",
  "text_id": "TXT123",
  "span": {"start": 100, "end": 150},
  "text": "Variant reading found in manuscript B",
  "metadata": {}
}
```

### Yigchung Mark

```http
GET /v2/yigchungs/{yigchung_id}
DELETE /v2/yigchungs/{yigchung_id}
```

Response:

```json
{
  "id": "MRK123",
  "edition_id": "ED123",
  "text_id": "TXT123",
  "span": {"start": 200, "end": 205},
  "metadata": null
}
```

## Example Calls

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/editions/ED123/segmentations" \
  -H "X-API-Key: your_api_key"

curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/editions/ED123/durchens" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '[
    {
      "span": {"start": 100, "end": 150},
      "text": "Variant reading found in manuscript B"
    },
    {
      "span": {"start": 300, "end": 330},
      "text": "Second variant reading"
    }
  ]'

curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/durchens/DUR123" \
  -H "X-API-Key: your_api_key"

curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/editions/ED123/yigchungs" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '[
    {"span": {"start": 200, "end": 205}},
    {"span": {"start": 350, "end": 360}}
  ]'

curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/editions/ED123/table-of-contents" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {"name": "Main sa bcad"},
    "sections": [
      {
        "title": {"en": "Chapter 1"},
        "span": {"start": 0, "end": 1200}
      }
    ]
  }'
```

## Developer Notes

- Collection routes live in `routers/editions.py`.
- Direct-by-ID routes live in `routers/annotation/`.
- Models live in `models/annotation.py`.
- Content changes through `PATCH /v2/editions/{edition_id}/content` adjust affected spans automatically.
- Neo4j deployment for Yigchung support must apply `database/neo4j_constraints.cypher` and reinstall triggers. Creation automatically merges the `yigchung` mark type.
