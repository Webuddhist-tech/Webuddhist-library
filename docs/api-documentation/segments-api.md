# Segments API Documentation

This document provides comprehensive documentation for all Segments-related endpoints in the OpenPecha API v2.

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Segment Endpoints](#segment-endpoints)
   - [Get Segment](#get-segment)
   - [Get Segment Content](#get-segment-content)
   - [Find Related Segments](#find-related-segments)
   - [Search Edition Content](#search-edition-content)
   - [Tag and Untag Segment](#tag-and-untag-segment)
---

## Overview

Segments are portions of edition content defined by character spans (one or more ranges). They are created as part of segmentations and enable fine-grained text operations, alignment between texts, and targeted content retrieval.

### Key Concepts

- **Segment**: A logical unit of text defined by one or more character spans
- **Segment Type**: A constrained string describing the segment's role: `paragraph`, `verse`, `title`, `back_matter`, `front_matter`, or `top_segment`
- **Lines**: Character spans that define segment boundaries; multiple lines must be sorted and adjacent
- **Segmentation**: Collection of segments that divide an edition's content
- **Alignment**: Direct `ALIGNED_TO` relationship between existing segments
- **Related Segments**: Segments that are aligned together across editions

### Segment Creation

Segments are not created directly. They are created as part of:
1. **Edition segmentation** via `POST /v2/editions/{edition_id}/segmentation`
2. **Edition creation** with inline annotations via `POST /v2/texts/{text_id}/editions`

Edition-pair alignment endpoints link existing segments; they do not create new segments.

### Base URL

```
Development: https://api-l25bgmwqoa-uc.a.run.app
Production: https://api-aq25662yyq-uc.a.run.app
Test: https://api-kwgjscy6gq-uc.a.run.app
Local: http://127.0.0.1:8000
```

---

## Authentication

All API requests require authentication using an API key.

**Header:**
```
X-API-Key: your_api_key_here
```

`X-Application` is optional on segment, segment content, and related-segment reads. When supplied, tag IDs are filtered to the requested application.

---

## Segment Endpoints

### Get Segment

Retrieve a segment with its segmentation, edition, text, line spans, and optional tag context.

**Endpoint:**
```
GET /v2/segments/{segment_id}
```

**Parameters:**

| Name | Type | Location | Required | Description |
|------|------|----------|----------|-------------|
| `segment_id` | string | path | Yes | The ID of the segment |

**Response: 200 OK**

```json
{
  "id": "SEG001",
  "segmentation_id": "SGN12345678",
  "edition_id": "ED12345678",
  "text_id": "TXT12345678",
  "type": "paragraph",
  "lines": [
    {"start": 0, "end": 100}
  ],
  "tag_ids": ["TAG123"]
}
```

`type` is always present and defaults to `"paragraph"` for segments created without an explicit type. `tag_ids` is omitted when the segment has no tags. If `X-Application` is supplied, returned tag IDs are filtered to tags belonging to that application.

**Error Responses:**
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `404 Not Found`: Segment does not exist

**Example Usage:**

```bash
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/segments/SEG001" \
  -H "X-API-Key: your_api_key"
```

---

### Get Segment Content

Retrieve the base text content for a segment based on its character span(s).

**Endpoint:**
```
GET /v2/segments/{segment_id}/content
```

**Parameters:**

| Name | Type | Location | Required | Description |
|------|------|----------|----------|-------------|
| `segment_id` | string | path | Yes | The ID of the segment |

**Response: 200 OK**

```json
"This is the text content of the segment."
```

**Response Type:** Plain text string (JSON-encoded)

The response contains text extracted from the immutable edition object associated with the segment's validated spans. Concurrent changes can return `409`; retry the read.

**Error Responses:**
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `404 Not Found`: Segment does not exist
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/segments/SEG001/content" \
  -H "X-API-Key: your_api_key"
```

**Use Cases:**
- Display segment text in UI
- Export segment content for analysis
- Retrieve aligned text portions
- Build parallel text views

---

### Find Related Segments

Find segments connected by up to five `ALIGNED_TO` hops in either direction. Traversal includes intermediate segments that do not match the display filters. Results are ordered consistently and paginated after traversal.

Traversal explores up to five alignment hops and visits each segment once. Pagination limits the returned results, not the number of segments explored. Concurrent coordinate/alignment changes can return `409`; retry the read. A base language filter includes variants, while a full language tag selects that variant, case-insensitively.

**Endpoint:**
```
GET /v2/segments/{segment_id}/related
```

**Parameters:**

| Name | Type | Location | Required | Default | Description |
|------|------|----------|----------|---------|-------------|
| `segment_id` | string | path | Yes | - | The ID of the segment to find related segments for |
| `limit` | integer | query | No | 20 | Number of related segments to return (1-100) |
| `offset` | integer | query | No | 0 | Number of related segments to skip |
| `text_id` | string | query | No | - | Filter related segments to one text |
| `edition_id` | string | query | No | - | Filter related segments to one edition |
| `language` | string | query | No | - | Filter related segments by text language code |

**Response: 200 OK**

```json
{
  "items": [
    {
      "id": "SEG001",
      "segmentation_id": "SGN12345678",
      "edition_id": "ED12345678",
      "text_id": "TXT12345678",
      "type": "paragraph",
      "lines": [{"start": 0, "end": 100}],
      "tag_ids": ["TAG123"]
    }
  ],
  "has_more": false,
  "offset": 0,
  "limit": 20
}
```

**Response Structure:**
- Returns a paginated object with flat segment objects in `items`
- Each segment includes `segmentation_id`, `edition_id`, `text_id`, and `type`
- Optional filters are applied to related display results before pagination; combined filters are conjunctive
- Empty result uses `"items": []`

**Error Responses:**
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `422 Validation Error`: Invalid `limit` or `offset`
- `500 Server Error`: Internal server error

If the segment ID does not exist, this endpoint returns an empty paginated response instead of `404`.

**Example Usage:**

```bash
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/segments/SEG001/related" \
  -H "X-API-Key: your_api_key"
```

```bash
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/segments/SEG001/related?text_id=TXT456&language=en" \
  -H "X-API-Key: your_api_key"
```

**Use Cases:**
- Find translation segments for source text segment
- Navigate between aligned editions
- Display parallel text views
- Build cross-edition references

---

### Search Edition Content

Use content search to find passages and their segment IDs:

```http
GET /v2/content-search
```

The endpoint supports `exact` (default) and `similar` search, with optional `text_id` and `edition_id` filters. It returns an array of results containing edition and text IDs, `segment_ids`, context, and spans. See the [Content Search API](content-search-api.md) for the full request and response contract.

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/content-search?query=Heart%20Sutra&search_type=similar&limit=20" \
  -H "X-API-Key: your_api_key"
```

---

### Tag and Untag Segment

Attach or remove an application tag from a segment.

**Endpoints:**

```
POST /v2/segments/{segment_id}/tags/{tag_id}
DELETE /v2/segments/{segment_id}/tags/{tag_id}
```

**Response: 204 No Content**

```bash
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/segments/SEG001/tags/TAG123" \
  -H "X-API-Key: your_api_key"

curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/segments/SEG001/tags/TAG123" \
  -H "X-API-Key: your_api_key"
```

**Developer Notes:**
- Implemented in `routers/segments.py`.
- Segment response models live in `models/annotation.py`.
- Content search is implemented in `routers/content_search.py` and `content_search/service.py`, with response models in `models/content_search.py`.
