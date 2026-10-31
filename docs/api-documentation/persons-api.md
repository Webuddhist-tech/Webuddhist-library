# Persons API Documentation

This document provides comprehensive documentation for all Persons-related endpoints in the OpenPecha API v2.

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Person Endpoints](#person-endpoints)
   - [List All Persons](#list-all-persons)
   - [Get Person by ID](#get-person-by-id)
   - [Create New Person](#create-new-person)
   - [Update Person](#update-person)
---

## Overview

Persons represent individuals who have contributed to texts as authors, translators, revisers, or scholars. The Persons API provides endpoints for managing person records with localized names and external identifiers (BDRC, Wikidata).

### Key Concepts

- **Person**: An individual who has contributed to one or more texts
- **Contributions**: Roles a person plays in relation to texts (author, translator, reviser, scholar)
- **Localized Names**: Support for names in multiple languages and scripts
- **External Identifiers**: Integration with BDRC and Wikidata

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

---

## Person Endpoints

### List All Persons

Retrieve all persons from the database with optional pagination and filtering.

**Endpoint:**
```
GET /v2/persons
```

**Parameters:**

| Name | Type | Location | Required | Default | Description |
|------|------|----------|----------|---------|-------------|
| `limit` | integer | query | No | 20 | Number of results per page (1-100) |
| `offset` | integer | query | No | 0 | Number of results to skip |
| `name` | string | query | No | - | Search by name via catalog search: lenient, script-aware matching (diacritic-insensitive, Sanskrit and Tibetan phonetic) across both primary and alternative names; minimum 2 characters. See [Catalog Search API](./catalog-search-api.md). |
| `bdrc` | string | query | No | - | Filter by BDRC ID (exact match) |
| `wiki` | string | query | No | - | Filter by Wikidata ID (exact match) |

**Response: 200 OK**

```json
{
  "items": [
    {
      "id": "P12345678",
      "name": {
        "en": "John Doe",
        "bo": "ཇོན་དོ།"
      },
      "alt_names": [{"en": "J. Doe", "bo": "ཇོན།"}],
      "bdrc": "P123456",
      "wiki": "Q123456"
    }
  ],
  "has_more": true,
  "offset": 0,
  "limit": 20
}
```

**Error Responses:**
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `422 Validation Error`: Invalid `limit` or `offset`, or `name` shorter than 2 characters
- `503 Service Unavailable`: `name` search was requested but catalog search is not configured
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Get all persons (default pagination)
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/persons" \
  -H "X-API-Key: your_api_key"

# Get persons with pagination
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/persons?limit=50&offset=100" \
  -H "X-API-Key: your_api_key"

# Search by name (lenient, script-aware match; minimum 2 characters)
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/persons?name=Nagarjuna" \
  -H "X-API-Key: your_api_key"

# Filter by BDRC ID (exact match)
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/persons?bdrc=P2816" \
  -H "X-API-Key: your_api_key"

# Filter by Wikidata ID (exact match)
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/persons?wiki=Q182485" \
  -H "X-API-Key: your_api_key"

# Combine filters with pagination
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/persons?name=Rinpoche&limit=30" \
  -H "X-API-Key: your_api_key"
```

---

### Get Person by ID

Fetch a specific person by their ID.

**Endpoint:**
```
GET /v2/persons/{person_id}
```

**Parameters:**

| Name | Type | Location | Required | Description |
|------|------|----------|----------|-------------|
| `person_id` | string | path | Yes | The ID of the person to retrieve |

**Response: 200 OK**

```json
{
  "id": "P12345678",
  "name": {
    "en": "John Doe",
    "bo": "ཇོན་དོ།"
  },
  "alt_names": [
    {
      "en": "J. Doe",
      "bo": "ཇོན།"
    },
    {
      "en": "John D."
    }
  ],
  "bdrc": "P123456",
  "wiki": "Q123456"
}
```

**Error Responses:**
- `404 Not Found`: Person does not exist
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/persons/P12345678" \
  -H "X-API-Key: your_api_key"
```

---

### Create New Person

Create a new person record with localized names and external identifiers.

**Endpoint:**
```
POST /v2/persons
```

**Request Body:**

```json
{
  "name": {
    "en": "John Doe",
    "bo": "ཇོན་དོ།"
  },
  "alt_names": [
    {
      "en": "J. Doe",
      "bo": "ཇོན།"
    }
  ],
  "bdrc": "P123456",
  "wiki": "Q123456"
}
```

**Required Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | object | Localized name (language code → name mapping, at least one language required) |

**Optional Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `alt_names` | array | Alternative localized names |
| `bdrc` | string | BDRC person identifier (must be unique) |
| `wiki` | string | Wikidata identifier (must be unique) |

**Validation Rules:**
- Localized strings must contain at least one language entry and non-empty values.
- Extra fields are rejected.
- Alternative names matching the primary name are removed; duplicate alternative names are de-duplicated.

**Response: 201 Created**

```json
{
  "id": "P12345678"
}
```

**Error Responses:**
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `409 Conflict`: Person with BDRC ID already exists
- `422 Validation Error`: Validation failed, missing required fields, empty localized strings, or extra fields
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Create person with basic info
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/persons" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": {
      "en": "Nagarjuna",
      "bo": "ཀླུ་སྒྲུབ།",
      "sa": "नागार्जुन"
    },
    "bdrc": "P2816",
    "wiki": "Q182485"
  }'

# Create person with alternative names
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/persons" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": {
      "en": "Tsongkhapa",
      "bo": "ཙོང་ཁ་པ།"
    },
    "alt_names": [
      {
        "en": "Je Tsongkhapa",
        "bo": "རྗེ་ཙོང་ཁ་པ།"
      },
      {
        "en": "Lobsang Drakpa",
        "bo": "བློ་བཟང་གྲགས་པ།"
      }
    ],
    "bdrc": "P64",
    "wiki": "Q234330"
  }'

# Create person with minimal info
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/persons" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": {
      "en": "Unknown Translator"
    }
  }'
```

---

### Update Person

Partially update a person record. Only provided fields will be updated; omitted fields retain their current values.

**Endpoint:**
```
PATCH /v2/persons/{person_id}
```

**Parameters:**

| Name | Type | Location | Required | Description |
|------|------|----------|----------|-------------|
| `person_id` | string | path | Yes | The ID of the person to update |

**Request Body:**

All fields are optional. Only include fields you want to update.

```json
{
  "name": {
    "en": "Updated Name",
    "bo": "གསར་བསྒྱུར་མིང་།"
  },
  "alt_names": [
    {
      "en": "Alternative Name",
      "bo": "གཞན་མིང་།"
    }
  ],
  "bdrc": "P654321",
  "wiki": "Q999999"
}
```

**Updatable Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | object | Localized name (language code → name) |
| `alt_names` | array | Alternative localized names |
| `bdrc` | string | BDRC identifier (must be unique) |
| `wiki` | string | Wikidata identifier (must be unique) |

**PATCH Rules:**
- At least one field must be provided.
- `null` values are rejected; omit fields you do not want to change.
- Extra fields are rejected.

**Response: 200 OK**

```json
{
  "id": "P12345678",
  "name": {
    "en": "Updated Name",
    "bo": "གསར་བསྒྱུར་མིང་།"
  },
  "alt_names": [
    {
      "en": "Alternative Name",
      "bo": "གཞན་མིང་།"
    }
  ],
  "bdrc": "P654321",
  "wiki": "Q123456"
}
```

**Error Responses:**
- `404 Not Found`: Person does not exist
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `409 Conflict`: BDRC ID or Wiki ID already exists for another person
- `422 Validation Error`: Validation failed, empty patch body, null fields, empty localized strings, or extra fields
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Update BDRC ID only
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/persons/P12345678" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "bdrc": "P654321"
  }'

# Update name only
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/persons/P12345678" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": {
      "en": "Updated Name",
      "bo": "གསར་བསྒྱུར་མིང་།"
    }
  }'

# Update multiple fields
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/persons/P12345678" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": {
      "en": "New Name"
    },
    "bdrc": "P999999",
    "wiki": "Q999999",
    "alt_names": [
      {
        "en": "Alternative Name",
        "bo": "གཞན་མིང་།"
      }
    ]
  }'

# Add Wikidata identifier to existing person
curl -X PATCH "https://api-l25bgmwqoa-uc.a.run.app/v2/persons/P12345678" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "wiki": "Q182485"
  }'
```

---

### Delete Person

Delete a person if no text contribution references them.

**Endpoint:**
```
DELETE /v2/persons/{person_id}
```

**Response: 204 No Content**

No response body is returned.

**Error Responses:**
- `404 Not Found`: Person does not exist
- `409 Conflict`: Person is referenced by one or more `Contribution` nodes
- `401 Unauthorized`: Missing or invalid API key in deployed environments

**Example Usage:**

```bash
curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/persons/P12345678" \
  -H "X-API-Key: your_api_key"
```

**Delete Behavior:**
- Deletes the `Person` node.
- Deletes the person's primary and alternative name `Nomen` and `LocalizedText` subgraphs.
- Does not delete texts, works, editions, or contributions. If contributions exist, deletion is blocked.

**Developer Notes:**
- Implemented in `routers/persons.py`.
- Models live in `models/person.py`.
- List filters are grouped in `PersonsQueryParams`.
- Persistence and uniqueness checks live behind `db.person`.
---
