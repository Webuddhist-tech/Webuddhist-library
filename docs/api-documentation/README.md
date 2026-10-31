# OpenPecha API v2

This folder documents the public FastAPI routes exposed by the backend. All endpoint paths below are relative to the API host.

## Base URLs

```text
Development: https://api-l25bgmwqoa-uc.a.run.app
Production: https://api-aq25662yyq-uc.a.run.app
Test: https://api-kwgjscy6gq-uc.a.run.app
Local FastAPI app: http://127.0.0.1:8000
```

## Authentication

Most routes require `X-API-Key`. In local test/dev mode the dependency accepts requests without a real key, but deployed environments validate the header.

Application-scoped routes also require or accept `X-Application`:

- Required for `categories` and `tags`.
- Optional for `texts` and `segments`; when present, application-owned tags are filtered to that application.
- App-bound API keys must use the same `X-Application` value as the key's bound application.
- Category/tag writes enforce application ownership. Work tag replacement preserves other applications' tags. Both the current and replacement category must be authorized when recategorizing a Work.
- Application creation/deletion requires an unbound administrative key. Unbound keys can select a scope with `X-Application`; without it, their Work mutations apply across applications.

Common errors:

- `401` for missing or invalid API keys in deployed environments.
- `403` for unauthorized application-owned writes or application administration with a bound key.
- `404` when a requested resource or application does not exist.
- `409` for conflicts such as duplicate unique identifiers.
- `422` for Pydantic validation errors, missing required headers, invalid ranges, null values in PATCH requests, extra fields, or business validation errors.

## Common Data Shapes

Localized strings are objects keyed by language code:

```json
{
  "en": "Heart Sutra",
  "bo": "ཤེས་རབ་སྙིང་པོ།"
}
```

Strings are stored exactly as submitted, except application names, which are trimmed and lowercased before becoming the application ID and name. Every string field must contain at least one non-whitespace character, so `""` and `"   "` are both rejected with `422`. A value sent with surrounding whitespace is stored with it, so `"W22084 "` will not match a later lookup for `"W22084"`; callers should send values already normalized. The one exception is the `text` of a content patch, where a whitespace-only payload such as a single space is a legitimate edit.

Character spans use half-open ranges: `start` is inclusive and `end` is exclusive. A segment contains one or more continuous `lines`; multiple lines inside one segment must be sorted and adjacent.

Paginated list endpoints return:

```json
{
  "items": [],
  "has_more": false,
  "offset": 0,
  "limit": 20
}
```

## Active Route Inventory

The running application exposes its complete route inventory at `GET /openapi.json`, with interactive documentation at `GET /docs` and `GET /redoc`.

| Area | Guide |
|------|-------|
| Texts and their relationships | [Texts](texts-api.md), [Relations](relations-api.md) |
| Editions and annotations | [Editions](editions-api.md), [Annotations and alignments](annotations-api.md) |
| Segments and recordings | [Segments](segments-api.md), [Recordings](recordings-api.md) |
| Search | [Content search](content-search-api.md), [Catalog search](catalog-search-api.md) |
| People and languages | [Persons](persons-api.md), [Languages](languages-api.md) |
| Application taxonomy | [Applications](applications-api.md), [Categories](categories-api.md), [Tags](tags-api.md) |
| OpenAPI | [Schema](schema-api.md) |
