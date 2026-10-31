# OpenAPI Schema Documentation

This document provides documentation for the Schema endpoint in the OpenPecha API v2.

---

## Table of Contents

1. [Overview](#overview)
2. [Schema Endpoints](#schema-endpoints)
   - [Get OpenAPI Specification](#get-openapi-specification)
3. [Use Cases](#use-cases)

---

## Overview

FastAPI exposes the generated OpenAPI schema for the application. The current codebase does not register a custom `/v2/schema/openapi` route.

### Key Concepts

- **OpenAPI Specification**: A standard format (formerly Swagger) for describing REST APIs
- **JSON Format**: The specification is returned as JSON from FastAPI's built-in schema route
- **Machine-Readable**: Can be used by tools to generate client libraries, documentation, and more

### Base URL

```
Development: https://api-l25bgmwqoa-uc.a.run.app
Production: https://api-aq25662yyq-uc.a.run.app
Test: https://api-kwgjscy6gq-uc.a.run.app
Local: http://127.0.0.1:8000
```

---

## Schema Endpoints

### Get OpenAPI Specification

Retrieve the complete OpenAPI specification file for the API.

**Endpoint:**
```
GET /openapi.json
```

**Parameters:** None

**Authentication:** Not required

**Response: 200 OK**

Returns the OpenAPI specification document in JSON format.

**Content-Type:** `application/json`

**Response Structure:**

The response is a complete OpenAPI specification document containing:
- API metadata (version, title, description)
- Server URLs
- All available endpoints
- Request/response schemas
- Data models and components
- Authentication requirements
- Error response formats

**Example Response (partial):**

```json
{
  "openapi": "3.1.0",
  "info": {
    "title": "OpenPecha API",
    "version": "0.1.0"
  },
  "paths": {
    "/v2/texts": {
      "get": {
        "summary": "List all texts"
      }
    }
  },
  "components": {
    "schemas": {}
  }
}
```

**Error Responses:**
- `500 Server Error`: Internal server error

**Example Usage:**

```bash
# Download OpenAPI specification
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/openapi.json" \
  -o openapi.json

# View specification
curl -X GET "https://api-l25bgmwqoa-uc.a.run.app/openapi.json"

# Pretty-print the downloaded schema
python -m json.tool openapi.json > openapi.pretty.json
```

## Interactive Docs

FastAPI also exposes:

- `GET /docs` for Swagger UI.
- `GET /redoc` for ReDoc.

## Use Cases

- Generate typed clients from the live API schema.
- Inspect request and response models while developing new endpoints.
- Confirm the exact route list exposed by the deployed application.

---
