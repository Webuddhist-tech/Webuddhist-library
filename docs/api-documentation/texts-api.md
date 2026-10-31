# Texts API Documentation

This document provides comprehensive documentation for all Texts-related endpoints in the OpenPecha API v2.

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Text Endpoints](#text-endpoints)
  - [List All Texts](#list-all-texts)
  - [Get Text by ID](#get-text-by-id)
  - [Create New Text](#create-new-text)
  - [Update Text](#update-text)

## Overview

Texts (also known as Expressions in FRBR terminology) represent the intellectual content of a work independent of any specific physical edition. A text can have multiple editions, translations, and commentaries.

### Key Concepts

- **Text (Expression)**: The abstract intellectual content of a work
- **Edition (Manifestation)**: A specific physical or digital instantiation of a text
- **Translation**: A text translated into another language
- **Commentary**: A text that comments on or explains another text
- **Contribution**: Attribution of a person or AI to a text (author, translator, reviser, scholar)
- **Tags**: Application-scoped labels attached to the work behind a text

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

`X-Application` is optional on text reads and updates. When supplied, application-scoped tag IDs in responses are filtered to that application, and app-bound API keys must match the supplied application.

---

## Text Endpoints

### List All Texts

Retrieve all texts with optional filtering and pagination.

**Endpoint:**

```
GET /v2/texts
```

**Parameters:**


| Name          | Type    | Location | Required | Default | Description                                                                                            |
| ------------- | ------- | -------- | -------- | ------- | ------------------------------------------------------------------------------------------------------ |
| `limit`       | integer | query    | No       | 20      | Number of results per page (1-100)                                                                     |
| `offset`      | integer | query    | No       | 0       | Number of results to skip                                                                              |
| `language`    | string  | query    | No       | -       | Filter by language code                                                                                |
| `title`       | string  | query    | No       | -       | Search by title via catalog search: lenient, script-aware matching (diacritic-insensitive, Sanskrit and Tibetan phonetic) across both primary and alternative titles; minimum 2 characters. See [Catalog Search API](./catalog-search-api.md). |
| `category_id` | string  | query    | No       | -       | Filter by category ID                                                                                  |
| `tag_id`      | string  | query    | No       | -       | Comma-separated application tag IDs |
| `tag_id_match` | string | query    | No       | `all`   | Tag matching mode: `all` requires every listed tag; `any` requires at least one |
| `author_id`   | string  | query    | No       | -       | Filter by contributing author person ID                                                                |
| `bdrc`        | string  | query    | No       | -       | Filter by BDRC identifier                                                                              |
| `wiki`        | string  | query    | No       | -       | Filter by Wikidata identifier                                                                          |


**Response: 200 OK**

```json
{
  "items": [
    {
      "id": "ABC12345678",
      "title": {
        "en": "Sample Text",
        "bo": "དཔེ་མཚོན་ཚིག་སྒྲུབ།"
      },
      "language": "bo",
      "category_id": "CAT12345678",
      "contributions": [{"type": "person", "id": "P12345678", "role": "author"}],
      "license": "public",
      "commentaries": [],
      "translations": ["DEF87654321"],
      "editions": ["M12345678"],
      "tag_ids": ["TAG123"]
    }
  ],
  "has_more": true,
  "offset": 0,
  "limit": 20
}
```

**Error Responses:**

- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `422 Validation Error`: Invalid `limit` or `offset`, or `title` shorter than 2 characters
- `503 Service Unavailable`: `title` search was requested but catalog search is not configured
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Get all texts (default pagination)
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts" \
  -H "X-API-Key: your_api_key"

# Get texts with pagination
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?limit=50&offset=100" \
  -H "X-API-Key: your_api_key"

# Filter by language
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?language=bo" \
  -H "X-API-Key: your_api_key"

# Search by title (lenient, script-aware match; minimum 2 characters)
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?title=Heart%20Sutra" \
  -H "X-API-Key: your_api_key"

# Filter by category
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?category_id=CAT12345678" \
  -H "X-API-Key: your_api_key"

# Filter by BDRC identifier
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?bdrc=W123456" \
  -H "X-API-Key: your_api_key"

# Combine multiple filters
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?language=bo&category_id=CAT12345678&limit=10" \
  -H "X-API-Key: your_api_key"

# Filter by one tag
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?tag_id=TAG123" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"

# Filter by every listed tag (default matching mode)
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?tag_id=TAG123,TAG456" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"

# Filter by any listed tag
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts?tag_id=TAG123,TAG456&tag_id_match=any" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"
```

---

### Get Text by ID

Fetch a specific text by its text ID.

**Endpoint:**

```
GET /v2/texts/{text_id}
```

**Parameters:**


| Name      | Type   | Location | Required | Description                    |
| --------- | ------ | -------- | -------- | ------------------------------ |
| `text_id` | string | path     | Yes      | The ID of the text to retrieve |


**Response: 200 OK (Root Text)**

```json
{
  "id": "T12345678",
  "title": {
    "en": "Sample Text",
    "bo": "དཔེ་མཚོན་ཚིག་སྒྲུབ།"
  },
  "language": "bo",
  "category_id": "CAT12345678",
  "contributions": [
    {
      "type": "person", "id": "P12345678",
      "role": "author"
    }
  ],
  "bdrc": "W123456",
  "license": "public",
  "commentaries": ["C12345678"],
  "translations": ["TR12345678"],
  "editions": ["M12345678"]
}
```

**Response: 200 OK (Translation Text)**

```json
{
  "id": "TR12345678",
  "title": {
    "en": "English Translation"
  },
  "language": "en",
  "category_id": "CAT12345678",
  "translation_of": "T12345678",
  "contributions": [
    {
      "type": "person", "id": "P87654321",
      "role": "translator"
    }
  ],
  "license": "cc0",
  "commentaries": [],
  "translations": [],
  "editions": []
}
```

**Error Responses:**

- `404 Not Found`: Text does not exist
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/T12345678" \
  -H "X-API-Key: your_api_key"
```

---

### Create New Text

Create a new text record with metadata and contributions.

**Endpoint:**

```
POST /v2/texts
```

**Request Body:**

```json
{
  "title": {
    "en": "English Title",
    "bo": "Tibetan Title"
  },
  "language": "bo",
  "category_id": "CAT12345678",
  "contributions": [
    {
      "type": "person", "id": "P12345678",
      "role": "author"
    }
  ],
  "bdrc": "W123456",
  "wiki": "Q123456",
  "date": "1200",
  "alt_titles": [
    {
      "en": "Alternative Title",
      "bo": "གཞན་མིང་།"
    }
  ],
  "license": "public",
  "tag_ids": ["TAG123"]
}
```

**Required Fields:**


| Field           | Type   | Description                                    |
| --------------- | ------ | ---------------------------------------------- |
| `title`         | object | Localized title (language code → text mapping) |
| `language`      | string | Primary language code (e.g., "bo", "en")       |
| `category_id`   | string | Required for original texts/commentaries; forbidden for translations |


**Optional Fields:**


| Field            | Type   | Description                                        |
| ---------------- | ------ | -------------------------------------------------- |
| `contributions`  | array  | Contributions (person or AI); omit when unknown    |
| `bdrc`           | string | BDRC identifier                                    |
| `wiki`           | string | Wikidata identifier                                |
| `date`           | string | Date of composition                                |
| `alt_titles`     | array  | Alternative localized titles                       |
| `translation_of` | string | Text ID this is a translation of                   |
| `commentary_of`  | string | Text ID this is a commentary of                    |
| `license`        | string | License type (see [License Types](#license-types)) |
| `tag_ids`        | array  | Tag IDs to attach to the work behind this text |

**Validation Rules:**

- `title` must contain a localized title for the text language or its base language. For example, language `bo-x-ewts` can use a `bo` title.
- A text cannot set both `translation_of` and `commentary_of`.
- Translations inherit their Work category. Supplying `category_id`, including `null` or the inherited value, returns `422`.
- Creation-time tags are added to the shared Work and must belong to the authorized application.
- `contributions` may be omitted or empty when attribution is unknown. Each entry must include either `type` with `id` or `bdrc_id` depending on contribution type.
- Extra fields are rejected.


#### Contributions

Contributions are optional. Each contribution must specify a role and either a person or AI identifier:

**Human Contribution:**

```json
{
  "type": "person", "id": "P12345678",
  "role": "author"
}
```

or

```json
{
  "type": "person", "bdrc_id": "P87654321",
  "role": "translator"
}
```

**AI Contribution:**

```json
{
  "type": "ai", "id": "gpt-4",
  "role": "translator"
}
```

**Contribution Roles:**

- `author`: Original author
- `translator`: Translator
- `reviser`: Editor/reviser
- `scholar`: Scholar

#### Creating Related Texts

**Translation:**

To create a translation, include the `translation_of` field with the source text ID:

```json
{
  "title": {
    "en": "English Translation"
  },
  "language": "en",
  "translation_of": "ABC12345678",
  "contributions": [
    {
      "type": "person", "bdrc_id": "P87654321",
      "role": "translator"
    }
  ],
  "license": "cc0"
}
```

**Commentary:**

To create a commentary, include the `commentary_of` field:

```json
{
  "title": {
    "bo": "འགྲེལ་པ།"
  },
  "language": "bo",
  "category_id": "CAT12345678",
  "commentary_of": "ABC12345678",
  "contributions": [
    {
      "type": "person", "id": "P12345678",
      "role": "author"
    }
  ]
}
```

**AI Translation:**

For AI-generated translations:

```json
{
  "title": {
    "en": "AI English Translation"
  },
  "language": "en",
  "translation_of": "ABC12345678",
  "contributions": [
    {
      "type": "ai", "id": "gpt-4",
      "role": "translator"
    }
  ],
  "license": "cc0"
}
```

**Response: 201 Created**

```json
{
  "id": "T12345678"
}
```

**Error Responses:**

- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `422 Validation Error`: Validation failed, missing required fields, invalid relation combination, invalid title language, invalid contributions, or extra fields
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Create a root text
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/texts" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "title": {
      "en": "Heart Sutra",
      "bo": "སྙིང་པོའི་མདོ།"
    },
    "language": "bo",
    "category_id": "CAT12345678",
    "contributions": [
      {
        "type": "person", "id": "P12345678",
        "role": "author"
      }
    ],
    "bdrc": "W123456",
    "license": "public"
  }'

# Create a translation
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/texts" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "title": {
      "en": "Heart Sutra - English Translation"
    },
    "language": "en",
    "translation_of": "T12345678",
    "contributions": [
      {
        "type": "person", "bdrc_id": "P87654321",
        "role": "translator"
      }
    ],
    "license": "cc-by"
  }'
```

---

### Delete Text

Delete a text when it has no editions and no incoming translation/commentary relationships.

**Endpoint:**

```
DELETE /v2/texts/{text_id}
```

**Response: 204 No Content**

No response body is returned.

**Error Responses:**

- `404 Not Found`: Text does not exist
- `409 Conflict`: Text has editions, translations, or commentaries pointing to it
- `401 Unauthorized`: Missing or invalid API key in deployed environments

**Example Usage:**

```bash
curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/T12345678" \
  -H "X-API-Key: your_api_key"
```

**Delete Behavior:**
- Deletes the `Text`, title/alternative-title `Nomen` and `LocalizedText` subgraphs, and contribution nodes attached to the text.
- Blocks if any edition belongs to the text.
- Blocks if another text points to this text through `TRANSLATION_OF` or `COMMENTARY_OF`.
- Deletes the underlying `Work` only when no other text still points to that work.
- If the work is preserved because other texts still use it, existing `HAS_TAG` relationships on the work are preserved.

---

### Update Text

Partially update a text record. Only provided fields will be updated; omitted fields retain their current values.

**Endpoint:**

```
PATCH /v2/texts/{text_id}
```

**Parameters:**


| Name      | Type   | Location | Required | Description                  |
| --------- | ------ | -------- | -------- | ---------------------------- |
| `text_id` | string | path     | Yes      | The ID of the text to update |


**Request Body:**

All fields are optional. Only include fields you want to update.

```json
{
  "title": {
    "en": "Updated Title",
    "bo": "གསར་བསྒྱུར་མཚན་བྱང་།"
  },
  "bdrc": "W654321",
  "wiki": "Q999999",
  "date": "1250",
  "language": "bo",
  "category_id": "CAT87654321",
  "expected_category_id": "CAT12345678",
  "alt_titles": [
    {
      "en": "Alternative Title",
      "bo": "མཚན་བྱང་གཞན།"
    }
  ],
  "license": "cc0",
  "contributions": [{"type": "person", "id": "P12345678", "role": "author"}],
  "tag_ids": ["TAG123"]
}
```

**Updatable Fields:**


| Field           | Type   | Description                                   |
| --------------- | ------ | --------------------------------------------- |
| `title`         | object | Localized title (language code → text)        |
| `alt_titles`    | array  | Alternative localized titles                  |
| `language`      | string | Primary language code                         |
| `category_id`   | string | New category for the shared Work and all its translations |
| `expected_category_id` | string | Current category, required when changing it; stale or missing values return `409` |
| `bdrc`          | string | BDRC identifier                               |
| `wiki`          | string | Wikidata identifier                           |
| `date`          | string | Date of composition                           |
| `license`       | string | License type                                  |
| `contributions` | array  | Replaces the text's current contributions     |
| `tag_ids`       | array  | Replaces only the authorized application's Work tags; other applications' tags remain |


**Note:** You cannot update `translation_of` or `commentary_of` via PATCH. These are set during creation. PATCH requires at least one field, rejects `null`, and rejects extra fields.

`contributions` replaces the text's whole contribution list. `tag_ids` replaces the Work tags within the authorized application scope, preserving tags owned by other applications; `[]` clears that scope. Category changes require ownership of both the existing and replacement category. Reassigning the same category is a no-op and does not require `expected_category_id`.

**Response: 200 OK**

```json
{
  "id": "T12345678",
  "title": {
    "en": "Updated Title",
    "bo": "གསར་བསྒྱུར་མཚན་བྱང་།"
  },
  "language": "bo",
  "category_id": "CAT12345678",
  "contributions": [
    {
      "type": "person", "id": "P12345678",
      "role": "author"
    }
  ],
  "bdrc": "W654321",
  "wiki": "Q123456",
  "license": "cc0",
  "commentaries": [],
  "translations": [],
  "editions": [],
  "tag_ids": ["TAG123"]
}
```

**Error Responses:**

- `404 Not Found`: Text does not exist
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `422 Validation Error`: Validation failed, empty patch body, null fields, or extra fields
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Update BDRC ID only
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/T12345678" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "bdrc": "W654321"
  }'

# Update title only
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/T12345678" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "title": {
      "en": "New Title",
      "bo": "མཚན་བྱང་གསར་པ།"
    }
  }'

# Update multiple fields
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/T12345678" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "title": {
      "en": "New Title"
    },
    "bdrc": "W999999",
    "wiki": "Q999999",
    "license": "cc-by-sa"
  }'

# Replace tag IDs
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/T12345678" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "tag_ids": ["TAG123", "TAG456"]
  }'
```

---

### Tag and Untag Text

Attach or remove a tag from the work behind a text.

**Endpoints:**

```
POST /v2/texts/{text_id}/tags/{tag_id}
DELETE /v2/texts/{text_id}/tags/{tag_id}
```

**Response: 204 No Content**

```bash
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/T12345678/tags/TAG123" \
  -H "X-API-Key: your_api_key"

curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/T12345678/tags/TAG123" \
  -H "X-API-Key: your_api_key"
```

**Developer Notes:**
- Implemented in `routers/texts.py`.
- Models live in `models/text.py` and `models/contribution.py`.
- List filters are defined in `TextsQueryParams`.
- Edition creation is mounted under the texts router because an edition always belongs to one text.

### License Types

Accepted license values are:

- `cc0`
- `public`
- `cc-by`
- `cc-by-sa`
- `cc-by-nd`
- `cc-by-nc`
- `cc-by-nc-sa`
- `cc-by-nc-nd`
- `copyrighted`
- `unknown`


