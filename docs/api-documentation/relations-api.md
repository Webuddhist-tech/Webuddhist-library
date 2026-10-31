# Relations Documentation

The current FastAPI application does not register a `/v2/relations/...` router. Older documentation referenced `GET /v2/relations/expressions/{text_id}`, but that endpoint is not available in the current codebase.

## How Text Relations Work Now

Text-to-text relationships are represented directly on text records:

- `translation_of`: set on a text that is a translation of another text.
- `commentary_of`: set on a text that comments on another text.
- `translations`: returned on a source text as IDs of translation texts.
- `commentaries`: returned on a source text as IDs of commentary texts.

Create a translation:

```json
{
  "title": {"en": "Heart Sutra Translation"},
  "language": "en",
  "translation_of": "TXT_SOURCE",
  "contributions": [
    {"type": "person", "id": "PERSON123", "role": "translator"}
  ]
}
```

Create a commentary:

```json
{
  "title": {"bo": "འགྲེལ་པ།"},
  "language": "bo",
  "category_id": "CAT123",
  "commentary_of": "TXT_SOURCE",
  "contributions": [
    {"type": "person", "id": "PERSON123", "role": "author"}
  ]
}
```

Fetch relation information:

```bash
curl "https://api-l25bgmwqoa-uc.a.run.app/v2/texts/TXT_SOURCE" \
  -H "X-API-Key: your_api_key"
```

Example response excerpt:

```json
{
  "id": "TXT_SOURCE",
  "translations": ["TXT_TRANSLATION"],
  "commentaries": ["TXT_COMMENTARY"]
}
```

## Related Editions

For edition-level relationships, use:

```http
GET /v2/editions/{edition_id}/related
```

This returns editions related through alignment or through the related texts behind those editions.

## Developer Notes

- Text relation fields are modeled in `models/text.py`.
- Text creation and retrieval are implemented in `routers/texts.py`.
- Related edition lookup is implemented in `routers/editions.py` and `database/edition_database.py`.
