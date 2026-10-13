# Editions API Documentation

Editions are concrete versions of a text. The metadata lives in Neo4j and the base text content is stored separately through the storage layer. An edition is always created under a text.

## Concepts

- **Diplomatic edition**: A transcription of a source. Requires `metadata.type = "diplomatic"`, a `bdrc` value, and a `pagination` annotation at creation.
- **Critical edition**: A scholarly edition. Must not include `bdrc`, and requires a `segmentation` annotation at creation.
- **Collated edition**: Accepted by the metadata enum, but the create request does not enforce initial pagination or segmentation for this type.
- **Content operations**: Insert, delete, and replace operations update stored base text and adjust stored spans for annotations on the edition.
- **Related editions**: Discovered from alignment and text relationship data.

## Authentication

Use `X-API-Key` in deployed environments.

```text
X-API-Key: your_api_key
```

## Get Edition Metadata

```http
GET /v2/editions/{edition_id}
```

Returns metadata for one edition.

```json
{
  "id": "ED123",
  "text_id": "TXT123",
  "type": "diplomatic",
  "source": "Derge Kangyur",
  "bdrc": "W22084",
  "wiki": null,
  "colophon": "Colophon text",
  "incipit_title": {
    "bo": "འདི་སྐད་བདག་གིས།"
  },
  "alt_incipit_titles": null
}
```

`404` means the edition does not exist.

## Get Edition Content

```http
GET /v2/editions/{edition_id}/content
```

Returns the stored base text as a JSON string. If both `span_start` and `span_end` are supplied, the returned string is sliced with Python-style half-open indexing.

| Query | Type | Required | Description |
|-------|------|----------|-------------|
| `span_start` | integer | No | Start character offset, inclusive |
| `span_end` | integer | No | End character offset, exclusive |

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/editions/ED123/content?span_start=0&span_end=100" \
  -H "X-API-Key: your_api_key"
```

## Patch Edition Content

```http
PATCH /v2/editions/{edition_id}/content
```

Applies one text operation and returns `204 No Content`. The request body is the operation object itself.

Insert:

```json
{
  "type": "insert",
  "position": 10,
  "text": "inserted text"
}
```

Delete:

```json
{
  "type": "delete",
  "start": 10,
  "end": 20
}
```

Replace:

```json
{
  "type": "replace",
  "start": 10,
  "end": 20,
  "text": "replacement text"
}
```

Validation rules:

- `start`, `end`, and `position` are character offsets.
- `start` must be less than `end`.
- Insert and replace text must be non-empty.
- Extra fields are rejected.
- An operation reaching past the end of the content is rejected with `422`.

Subsequent annotation spans are validated against the patched text, not the original.

The database span adjustment is performed before the storage write. If the storage write fails, the code compensates the span adjustment before re-raising.

## Delete Edition

```http
DELETE /v2/editions/{edition_id}
```

Deletes the edition metadata and associated annotation data handled by the database layer. Successful deletion returns `204 No Content`.

Delete behavior:

- Deletes the `Edition` node and its incipit title `Nomen` and `LocalizedText` subgraphs.
- Cascade-deletes segmentations, alignments, pagination, table of contents, bibliographic metadata, durchen notes, yigchung marks, recordings, spans, segments, pages, volumes, and table of contents sections associated with the edition, including annotations added after edition creation.
- Deletes the edition's `HAS_SOURCE` relationship, but preserves the `Source` node.
- Does not delete the parent `Text`, underlying `Work`, categories, tags, contributors, or lookup/type nodes.
- Does not delete stored base text, recording audio files, or other non-database side effects.

Error responses:

- `404 Not Found`: Edition does not exist.
- `401 Unauthorized`: Missing or invalid API key in deployed environments.

## List Editions for a Text

```http
GET /v2/texts/{text_id}/editions
```

Optional query:

| Query | Type | Description |
|-------|------|-------------|
| `edition_type` | `diplomatic`, `critical`, or `collated` | Filters returned editions by type |

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/TXT123/editions?edition_type=diplomatic" \
  -H "X-API-Key: your_api_key"
```

## Create Edition

```http
POST /v2/texts/{text_id}/editions
```

Creates an edition, stores its content, creates the required initial annotation, and schedules search segmentation in the background.

Diplomatic request:

```json
{
  "content": "Full diplomatic text content",
  "metadata": {
    "type": "diplomatic",
    "bdrc": "W22084",
    "source": "Derge Kangyur"
  },
  "pagination": {
    "volumes": [
      {
        "pages": [
          {
            "reference": "1a",
            "lines": [
              {"start": 0, "end": 100}
            ]
          }
        ]
      }
    ]
  }
}
```

Critical request:

```json
{
  "content": "Full critical text content",
  "metadata": {
    "type": "critical",
    "source": "OpenPecha critical edition",
    "incipit_title": {
      "bo": "འདི་སྐད་བདག་གིས།"
    }
  },
  "segmentation": {
    "segments": [
      {
        "lines": [
          {"start": 0, "end": 50}
        ]
      },
      {
        "lines": [
          {"start": 50, "end": 100}
        ]
      }
    ]
  }
}
```

Response:

```json
{
  "id": "ED123"
}
```

Important validation rules:

- `content` is required and must contain at least one non-whitespace character.
- `content` is stored exactly as submitted, including leading and trailing whitespace.
- Span offsets must not extend past the end of `content`.
- Diplomatic editions require `metadata.bdrc` and `pagination`; they must not include `segmentation`.
- Critical editions must not include `metadata.bdrc`; they require `segmentation` and must not include `pagination`.
- `alt_incipit_titles` can only be set when `incipit_title` is set.
- Every span must satisfy `0 <= start <= end`; see [span layout rules](#span-layout-rules).

### Span offset semantics

All `start` and `end` values are **Unicode code point offsets** into `content` exactly as
submitted, with `end` exclusive. Getting the unit wrong silently misaligns every annotation,
so note the following:

- Do not send UTF-8 byte offsets. Tibetan code points are three bytes each, so byte offsets
  run roughly 3× long and are rejected.
- Do not send grapheme cluster counts. A Tibetan stack with subjoined consonants is several
  code points but one visual unit.
- Normalize before computing offsets, and send the same form you measured. In romanized Pali,
  `ā` is one code point as NFC but two as NFD, so `sammāsambuddhassa` is 17 or 18 code points
  depending on form. The API stores content byte-for-byte and never normalizes it.
- JavaScript's `String.length` and Python's `len()` both work for Tibetan and romanized Pali,
  since those scripts lie in the Basic Multilingual Plane. They diverge only for characters
  above U+FFFF, such as emoji or Siddham.

### Span layout rules

A span is any range with `0 <= start <= end`. An empty span, where `start` equals `end`, marks a
position rather than covering text: it is how a blank folio and a heading with no content of its own
are recorded. A span with `start` greater than `end` is rejected with `422`.

Wherever spans appear as a list, that list must follow one of three layouts:

| Spans | Layout |
| --- | --- |
| `lines` within a page or segment | contiguous: each line starts exactly where the previous line ended |
| `pages` within a volume | contiguous |
| `volumes` within a pagination | sorted and non-overlapping, in volume index order; gaps are allowed |
| sections at one level of a table of contents | sorted and non-overlapping; gaps are allowed |
| `segments` within a segmentation | sorted by `start`; segments may overlap |

Two rules are not about layout. A table of contents subsection must sit inside its parent's span, and
volume indexes must form a continuous sequence from `1`. Content patch operations are the one place
where an empty range is refused, because deleting or replacing zero characters does nothing.

## Edition Annotation Collections

Annotations can be listed and created by type under an edition. Creation returns `{ "id": "..." }`.
Each edition has at most one segmentation. Large segment collections are paginated from the edition segmentation `/segments` endpoint.

Spans follow the [span offset semantics](#span-offset-semantics) above and are checked against the
edition's current content. A span reaching past the end of the content is rejected with `422` and
nothing is written.

### Segmentations

```http
GET /v2/editions/{edition_id}/segmentation
POST /v2/editions/{edition_id}/segmentation
```

GET response:

```json
{
  "id": "SGN123",
  "edition_id": "ED123",
  "text_id": "TXT123"
}
```

Use `GET /v2/editions/{edition_id}/segmentation/segments?limit=500&offset=0` to fetch paginated segment rows.

Request:

```json
{
  "segments": [
    {
      "reference": "1.1",
      "lines": [
        {"start": 0, "end": 50}
      ]
    }
  ]
}
```

### Alignments

```http
GET /v2/editions/{edition_id}/alignments
PUT /v2/editions/{source_edition_id}/alignments/{target_edition_id}
GET /v2/editions/{source_edition_id}/alignments/{target_edition_id}?limit=500&offset=0
DELETE /v2/editions/{source_edition_id}/alignments/{target_edition_id}
```

Alignments are no longer annotation resources. They are direct `ALIGNED_TO` relationships between existing source and target edition segments.

`GET /v2/editions/{edition_id}/alignments` returns directional contexts that include the requested edition:

```json
[
  {
    "aligned_edition_id": "ED_SOURCE",
    "aligned_text_id": "TXT_SOURCE",
    "target_edition_id": "ED_TARGET",
    "target_text_id": "TXT_TARGET"
  }
]
```

The client can infer whether the requested edition is on the source or target side by comparing `edition_id` to `aligned_edition_id` and `target_edition_id`.

PUT request:

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

`PUT` validates all references before changing relationships and returns `204 No Content`. Edition-pair `GET` returns paginated source/target `SegmentWithContextOutput` pairs.

### Pagination

```http
GET /v2/editions/{edition_id}/pagination
POST /v2/editions/{edition_id}/pagination
```

```json
{
  "volumes": [
    {
      "pages": [
        {
          "reference": "1a",
          "lines": [
            {"start": 0, "end": 500}
          ]
        }
      ]
    }
  ]
}
```

A single-volume pagination must omit `index`. Multi-volume pagination must use unique continuous indexes starting at `1`.

Page offsets are positions in the edition's single base text, not per-volume positions, so volumes
carve up that text between them. Each volume must therefore start at or after the previous volume
ends, and a pagination whose volumes overlap or run counter to their index order is rejected with
`422`. Adjacent volumes are fine: volume 2 may start exactly where volume 1 ends.

A folio with no text is a page whose single line is empty at the position where the folio sits, for
example `{"reference": "1b", "lines": [{"start": 8, "end": 8}]}` between a page ending at `8` and the
next page starting at `8`. Two blank folios at the same position are stored but their order between
each other is not preserved.

### Table of contents

```http
GET /v2/editions/{edition_id}/table-of-contents
POST /v2/editions/{edition_id}/table-of-contents
```

GET response:

```json
[
  {
    "id": "OUT123",
    "edition_id": "ED123",
    "text_id": "TXT123",
    "metadata": {
      "name": "Main sa bcad"
    },
    "sections": [
      {
        "id": "SEC123",
        "title": {
          "bo": "ལེའུ་དང་པོ།",
          "en": "Chapter 1"
        },
        "summary": {
          "en": "Opening topic"
        },
        "span": {"start": 0, "end": 1200},
        "subsections": []
      }
    ]
  }
]
```

Request:

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
          "span": {"start": 0, "end": 350}
        }
      ]
    }
  ]
}
```

Each section has a required localized `title`, optional localized `summary`, required `span`, and optional recursive `subsections`. Each subsection span must be fully contained inside its parent section span. Multiple table of contents can be attached to the same edition. Returned sections and subsections are ordered by their span start/end positions.

Sections at the same level must be sorted and must not overlap, so a table of contents is submitted in
document order. A heading that carries no content of its own — a title immediately followed by the next
heading — is an empty span at the position where it appears, such as `{"start": 229637, "end": 229637}`.

### Bibliographic Metadata

```http
GET /v2/editions/{edition_id}/bibliographic
POST /v2/editions/{edition_id}/bibliographic
```

```json
{
  "span": {"start": 5000, "end": 5500},
  "type": "colophon",
  "metadata": {}
}
```

Supported types: `colophon`, `incipit`, `alt_incipit`, `alt_title`, `person`, `title`, and `author`.

### Durchen Notes

```http
GET /v2/editions/{edition_id}/durchens
POST /v2/editions/{edition_id}/durchens
```

```json
[
  {
    "span": {"start": 100, "end": 150},
    "text": "Variant reading from witness B",
    "metadata": {}
  }
]
```

The array must contain at least one note. Creation is atomic and returns an array of `{ "id": "..." }` objects in request order.

## Recordings

```http
GET /v2/editions/{edition_id}/recordings
POST /v2/editions/{edition_id}/recordings
```

Audio readings of the edition, each crediting its narrator through the same `contributions` shape used by texts. Recordings are not annotations: they carry no spans, and creation is a `multipart/form-data` upload rather than a JSON body. An edition can have any number of them. See [recordings-api.md](recordings-api.md).

## Related Editions

```http
GET /v2/editions/{edition_id}/related
```

Returns editions related through alignment or text relationships.

## Developer Notes

- Router: `routers/editions.py`.
- Edition creation route: `routers/texts.py`.
- Models: `models/edition.py`, `models/annotation.py`, `models/content_operation.py`, `models/recording.py`, and `models/requests.py`.
- Storage dependency handles base text and recording audio reads/writes; database dependencies handle metadata, annotations, span adjustment, and related lookups.
