# Applications API Documentation

This document provides comprehensive documentation for all Applications-related endpoints in the OpenPecha API v2.

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Application Endpoints](#application-endpoints)
   - [Create New Application](#create-new-application)
   - [Delete Application](#delete-application)

---

## Overview

### Why Applications?

Applications provide:
1. **Content Isolation**: Separate category taxonomies for different platforms
2. **Multi-Tenancy**: Support multiple projects/services in one system
3. **Organized Namespaces**: Clear boundaries between different use cases


### Base URL

```
Development: https://api-l25bgmwqoa-uc.a.run.app
Production: https://api-aq25662yyq-uc.a.run.app
Test: https://api-kwgjscy6gq-uc.a.run.app
Local: http://127.0.0.1:8000
```

---

## Authentication

Creating or deleting an application requires an unbound API key. Application-bound keys receive `403 Forbidden`, including when their `X-Application` header matches the target application. Authentication is bypassed only in the configured development/test mode.

**Header:**
```
X-API-Key: your_api_key_here
```

---

## Application Endpoints

### Create New Application

Create a new application entry in the database. Input is trimmed and normalized to lowercase; the normalized value is used as both the application `id` and `name`.

**Endpoint:**
```
POST /v2/applications
```

**Request Body:**

```json
{
  "name": "MyApp"
}
```

**Request Schema:**

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `name` | string | Yes | minLength: 1 | Application name (will be lowercased) |

**Response: 201 Created**

```json
{
  "id": "myapp",
  "name": "myapp"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Application identifier (lowercase) |
| `name` | string | Application name (lowercase) |

**Key Behavior:**
- Input name is trimmed and converted to lowercase
- Both `id` and `name` are set to the normalized value
- Application IDs must be unique

**Error Responses:**
- `401 Unauthorized`: Missing or invalid API key in deployed environments
- `409 Conflict`: An application with the normalized name already exists
- `422 Validation Error`: Missing `name`, empty `name`, extra fields, or validation failure
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Create application
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/applications" \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "MyApp"
  }'

# Response: {"id": "342424sdr", "name": "webuddhist"}
```

**Use Cases:**
- Initialize new platform or project
- Set up content namespace
- Create isolated category taxonomy
- Enable multi-tenant architecture

**Developer Notes:**
- Implemented in `routers/applications.py`.
- Request model: `ApplicationCreateRequest`.
- Response model: `ApplicationResponse`.
- Application-scoped category and tag endpoints validate that the requested application exists.

---

### Delete Application

Delete an application only if nothing is bound to it.

**Endpoint:**
```
DELETE /v2/applications/{application_id}
```

**Response: 204 No Content**

No response body is returned.

**Error Responses:**
- `404 Not Found`: Application does not exist
- `409 Conflict`: Application still has tags, categories, or API keys bound to it
- `401 Unauthorized`: Missing or invalid API key in deployed environments

**Example Usage:**

```bash
curl -X DELETE "https://api-l25bgmwqoa-uc.a.run.app/v2/applications/myapp" \
  -H "X-API-Key: your_api_key"
```

**Delete Behavior:**
- Deletes only the `Application` node.
- Does not cascade-delete tags, categories, API keys, texts, works, or editions.
