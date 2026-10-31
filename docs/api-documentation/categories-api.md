# Categories API Documentation

This document provides comprehensive documentation for all Categories-related endpoints in the OpenPecha API v2.

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Category Endpoints](#category-endpoints)
   - [List Categories](#list-categories)
   - [Get Category by ID](#get-category-by-id)
   - [Create New Category](#create-new-category)
---

## Overview

Categories provide a hierarchical taxonomy system for organizing texts. Each category can have localized titles in multiple languages and can be part of a parent-child hierarchy for nested organization.

### Key Concepts

- **Category**: A classification or topic used to organize texts
- **Hierarchy**: Tree structure where categories can have parent and child categories
- **Application Context**: Categories are scoped to specific applications via the `X-Application` header
- **Localized Titles**: Support for category names in multiple languages

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

## Category Endpoints

### List Categories

Get categories filtered by application (via `X-Application` header) and optional parent. If `parent_id` is omitted, the endpoint returns root categories for the application.

**Endpoint:**
```
GET /v2/categories
```

**Headers:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `X-API-Key` | string | Yes | API authentication key |
| `X-Application` | string | Yes | Application context for categories |

**Parameters:**

| Name | Type | Location | Required | Default | Description |
|------|------|----------|----------|---------|-------------|
| `parent_id` | string | query | No | null | Parent category ID. Omit it to return root categories. |

**Response: 200 OK (Root Categories)**

```json
[
  {
    "id": "CAT12345678",
    "parent_id": null,
    "title": {
      "en": "Literature",
      "bo": "རྩོམ་རིག"
    },
    "children": ["CAT87654321"]
  },
  {
    "id": "CAT23456789",
    "parent_id": null,
    "title": {
      "en": "History",
      "bo": "ལོ་རྒྱུས"
    },
    "children": []
  }
]
```

**Response: 200 OK (Child Categories)**

```json
[
  {
    "id": "CAT87654321",
    "parent_id": "CAT12345678",
    "title": {
      "en": "Poetry",
      "bo": "སྙན་ངག"
    },
    "children": []
  }
]
```

**Error Responses:**
- `404 Not Found`: Application does not exist
- `422 Validation Error`: Missing `X-Application` header or invalid query parameter
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Get all root categories
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/categories" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"

# Get child categories of a specific parent
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/categories?parent_id=CAT12345678" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"

```

---

### Get Category by ID

Fetch one category by ID within the application selected by `X-Application`. A category that exists in a different application is treated as not found.

**Endpoint:**
```
GET /v2/categories/{category_id}
```

**Headers:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `X-API-Key` | string | Yes | API authentication key |
| `X-Application` | string | Yes | Application context for categories |

**Parameters:**

| Name | Type | Location | Required | Description |
|------|------|----------|----------|-------------|
| `category_id` | string | path | Yes | Category ID to retrieve |

**Response: 200 OK**

```json
{
  "id": "CAT12345678",
  "parent_id": null,
  "title": {
    "en": "Literature",
    "bo": "རྩོམ་རིག"
  },
  "description": {
    "en": "Literary works and compositions"
  },
  "children": ["CAT87654321"]
}
```

**Error Responses:**
- `404 Not Found`: Application or category does not exist
- `422 Validation Error`: Missing `X-Application` header
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/v2/categories/CAT12345678" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"
```

---

### Create New Category

Create a category with localized title and optional parent relationship. Application context is provided via `X-Application` header.

**Endpoint:**
```
POST /v2/categories
```

**Headers:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `X-API-Key` | string | Yes | API authentication key |
| `X-Application` | string | Yes | Application context for the category |

**Request Body:**

```json
{
  "title": {
    "en": "Literature",
    "bo": "རྩོམ་རིག"
  },
  "description": {
    "en": "Literary works and compositions",
    "bo": "རྩོམ་རིག་གི་བརྩམས་ཆོས།"
  },
  "parent_id": "CAT12345678"
}
```

**Required Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `title` | object | Localized category titles (language code → text mapping) |

**Optional Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `description` | object | Localized category descriptions (language code → text mapping) |
| `parent_id` | string | Parent category ID (null for root categories) |

**Response: 201 Created**

```json
{
  "id": "CAT12345678"
}
```

**Error Responses:**
- `404 Not Found`: Application or parent category does not exist
- `422 Validation Error`: Missing `X-Application` header, duplicate title in the same parent/application, or invalid body
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Create root category
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/categories" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist" \
  -H "Content-Type: application/json" \
  -d '{
    "title": {
      "en": "Literature",
      "bo": "རྩོམ་རིག"
    }
  }'

# Create child category with parent
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/categories" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist" \
  -H "Content-Type: application/json" \
  -d '{
    "title": {
      "en": "Poetry",
      "bo": "སྙན་ངག"
    },
    "parent_id": "CAT12345678"
  }'

# Create category with description
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/categories" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist" \
  -H "Content-Type: application/json" \
  -d '{
    "title": {
      "en": "Buddhist Philosophy",
      "bo": "ནང་པའི་གྲུབ་མཐའ།"
    },
    "description": {
      "en": "Philosophical texts from Buddhist traditions",
      "bo": "ནང་པའི་ལུགས་ཀྱི་གྲུབ་མཐའི་གཞུང་ལུགས།"
    }
  }'
```

---

### Delete Category

Delete a category and all of its child categories within the requested application.

**Endpoint:**
```
DELETE /v2/categories/{category_id}
```

**Headers:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `X-API-Key` | string | Yes | API authentication key |
| `X-Application` | string | Yes | Application context for the category |

**Response: 204 No Content**

No response body is returned.

**Error Responses:**
- `404 Not Found`: Application or category does not exist in the requested application
- `409 Conflict`: The category or one of its subcategories is still referenced by one or more works
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `422 Validation Error`: Missing `X-Application` header

**Example Usage:**

```bash
curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/categories/CAT12345678" \
  -H "X-API-Key: your_api_key" \
  -H "X-Application: webuddhist"
```

**Delete Behavior:**
- Recursively deletes the target category and its child categories in the same application.
- Deletes each removed category's title/description `Nomen` and `LocalizedText` subgraphs.
- Blocked with `409 Conflict` if the target category or any of its subcategories is still referenced by a work (every work must keep a category); reassign or delete those works first.
- Does not delete works, texts, editions, tags, or applications.

---

## Developer Notes

- Implemented in `routers/categories.py`.
- Models live in `models/category.py`.
- Category CRUD uses `X-Application` to enforce application isolation.
- `GET /v2/categories` returns only root categories when `parent_id` is omitted.
- `children` is a list of child category IDs, not embedded category objects.
