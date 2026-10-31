# Recordings API Documentation

A recording is an audio reading of an edition. The metadata lives in Neo4j and the audio file is stored separately through the storage layer. A recording is always created under an edition, and an edition can have any number of them.

## Concepts

- **Narrator**: The person or AI reading the text, credited through the same `contributions` shape used by texts. Every contribution on a recording must have role `narrator`; any other role is rejected with `422`.
- **Upload**: Audio is submitted as `multipart/form-data` in the same request that creates the metadata, so a recording never exists without its file.
- **Format**: Derived from the upload's content type, not from the filename. Supported formats are `mp3`, `wav`, `m4a`, `ogg`, and `flac`.
- **Playback**: The audio endpoint redirects to a short-lived presigned storage URL rather than proxying bytes, so players get range requests and seeking directly from storage.
- **Cues**: Optional time ranges of the audio, each mapped to the span of base text being recited during it, so a client can highlight the text as it is read. A span can be any length, from a verse down to a single syllable.

## Authentication

Use `X-API-Key` in deployed environments.

```text
X-API-Key: your_api_key
```

## List Recordings for an Edition

```http
GET /v2/editions/{edition_id}/recordings
```

Returns every recording of the edition, ordered by ID. An edition with no recordings returns `[]`.

```json
[
  {
    "id": "REC123",
    "edition_id": "ED123",
    "text_id": "TXT123",
    "title": {
      "en": "Chapter one, read aloud"
    },
    "language": "bo",
    "license": "cc-by",
    "date": "2024",
    "duration_ms": 754000,
    "format": "mp3",
    "size_bytes": 18432000,
    "contributions": [
      {
        "type": "person",
        "id": "PER123",
        "bdrc_id": "P1583",
        "role": "narrator",
        "name": {
          "bo": "མཁན་པོ་"
        }
      }
    ]
  }
]
```

`404` means the edition does not exist.

## Create Recording

```http
POST /v2/editions/{edition_id}/recordings
```

Sent as `multipart/form-data` with two parts:

| Part | Type | Required | Description |
|------|------|----------|-------------|
| `metadata` | string | Yes | JSON-encoded recording metadata |
| `audio` | file | Yes | The audio file, with an audio content type |

The `metadata` part:

```json
{
  "title": {
    "en": "Chapter one, read aloud"
  },
  "language": "bo",
  "license": "cc-by",
  "date": "2024",
  "duration_ms": 754000,
  "contributions": [
    {
      "type": "person",
      "id": "PER123",
      "role": "narrator"
    }
  ]
}
```

Only `contributions` is required; everything else is optional and `license` defaults to `public`.

```bash
curl -X POST "https://api-l25bgmwqoa-uc.a.run.app/v2/editions/ED123/recordings" \
  -H "X-API-Key: your_api_key" \
  -F 'metadata={"contributions":[{"type":"person","id":"PER123","role":"narrator"}]}' \
  -F "audio=@reading.mp3;type=audio/mpeg"
```

Response:

```json
{
  "id": "REC123"
}
```

Validation rules:

- `contributions` must contain at least one entry, and every entry must have role `narrator`. AI narrators use `{"type": "ai", "id": "...", "role": "narrator"}`.
- Referenced persons must already exist; a person can be identified by `id` or `bdrc_id`, but not both.
- The `audio` part must have a supported audio content type and a non-empty body.
- `language` is a BCP 47 tag whose base subtag must exist as a language in the database.
- `format` and `size_bytes` are derived from the upload and cannot be set by the caller.
- `duration_ms` is not derived from the file; supply it if you want it recorded.

Audio is uploaded to a fresh S3 key before metadata is committed. A failed request can leave an unused storage object, but cannot publish recording metadata for an unsuccessful upload.

Error responses:

- `404 Not Found`: Edition does not exist.
- `422 Validation Error`: Malformed metadata JSON, a non-narrator role, an empty `contributions` list, an unsupported or missing audio content type, an empty audio body, an unknown person, or an unknown language.

## Get Recording

```http
GET /v2/recordings/{recording_id}
```

Returns one recording in the same shape as the list endpoint. `404` means the recording does not exist.

## Get Recording Audio

```http
GET /v2/recordings/{recording_id}/audio
```

Responds `307 Temporary Redirect` with a `Location` header pointing at a presigned storage URL valid for one hour. Most HTTP clients follow this automatically; a client that needs the URL itself should disable redirect following and read the header.

Because the redirect target is served by storage rather than the API, range requests and seeking work without any further support from this service.

```bash
curl -L "https://api-l25bgmwqoa-uc.a.run.app/v2/recordings/REC123/audio" \
  -H "X-API-Key: your_api_key" \
  -o reading.mp3
```

## Update Recording

```http
PATCH /v2/recordings/{recording_id}
```

Partially updates metadata and returns the full updated recording. Accepts `title`, `language`, `license`, `date`, `duration_ms`, and `contributions`.

```json
{
  "title": {
    "en": "Chapter one, remastered"
  },
  "license": "cc-by-sa"
}
```

Supplying `contributions` replaces the whole list, and the narrator-only rule applies to the replacement. As everywhere else in the API, null values are rejected in `PATCH` and at least one field must be provided.

The audio file itself cannot be patched. Replace a recording's audio by deleting it and posting a new one.

## Delete Recording

```http
DELETE /v2/recordings/{recording_id}
```

Deletes the recording metadata, returning `204 No Content`. Its audio remains in storage.

Delete behavior:

- Deletes the `Recording` node, its `Contribution` nodes, its cues, and its title `Nomen` and `LocalizedText` subgraph.
- Retains the audio object in storage; the application does not clean up unreferenced files.
- Does not delete the parent `Edition`, the narrating `Person` or `AI`, or lookup nodes such as `RoleType`, `LicenseType`, and `Language`.

Deleting the parent edition cascade-deletes its recordings' database rows along with the rest of its annotations, but leaves their audio objects in storage, matching how edition deletion already treats base text.

## Recording Cues

A cue says that, between `start_ms` and `end_ms` of the audio, the narrator is reciting the base text in `span`. Spans use the same Unicode code point offsets into the edition content as every other annotation.

### Get Cues

```http
GET /v2/recordings/{recording_id}/cues
```

Returns every cue of the recording, ordered by `start_ms`, without pagination. A recording with no cues returns `[]`; `404` means the recording does not exist.

```json
[
  {"span": {"start": 0, "end": 42}, "start_ms": 0, "end_ms": 4200},
  {"span": {"start": 42, "end": 97}, "start_ms": 4200, "end_ms": 9100}
]
```

### Replace Cues

```http
PUT /v2/recordings/{recording_id}/cues
```

Replaces all cues of the recording and returns `204 No Content`. The request body is the array of cues, in the same shape `GET` returns.

```json
[
  {"span": {"start": 0, "end": 42}, "start_ms": 0, "end_ms": 4200},
  {"span": {"start": 42, "end": 97}, "start_ms": 4200, "end_ms": 9100}
]
```

Validation rules:

- The array must contain at least one cue. Use `DELETE` to remove all cues.
- `start_ms` must be less than `end_ms`.
- A span must cover at least one character and must lie within the edition content.
- Cues must be sorted by time and must not overlap in time. Gaps between cues are allowed.
- The same span may appear in several cues, which is how a refrain recited more than once is expressed.

Error responses:

- `404 Not Found`: Recording does not exist.
- `422 Validation Error`: Any of the rules above is violated.

### Delete Cues

```http
DELETE /v2/recordings/{recording_id}/cues
```

Deletes all cues of the recording and returns `204 No Content`. The recording and its audio are untouched.

### Content edits

Cue spans follow `PATCH /v2/editions/{edition_id}/content` with the same rules as notes. Only the span moves; the audio times never change.

- An edit before a cue shifts its span, so it keeps covering the same recited text. An edit after it leaves it unchanged.
- An insert, delete, or replace strictly inside a span resizes the span to cover the edited text.
- A delete or replace that covers the whole span deletes the cue. That stretch of audio then has no highlight.
- A delete that overlaps one end of a span trims the span to the text that remains. A replace does the same, except that a replace overlapping the end of the span keeps the replacement text inside it.

### Client playback

1. Fetch the edition's base text, for example through `GET /v2/editions/{edition_id}/content/url`.
2. Fetch the cues with `GET /v2/recordings/{recording_id}/cues`.
3. Play the audio from `GET /v2/recordings/{recording_id}/audio`.
4. On each time update, binary-search the cues by `start_ms` and highlight `content[span.start:span.end]` for the cue containing the current time. This works because cues are sorted and do not overlap.

## Developer Notes

- Routers: `routers/recordings.py`; the edition-scoped list and create routes live in `routers/editions.py`.
- Models: `models/recording.py` and `models/contribution.py`.
- Database: `database/recording_database.py`, with the shared `Contribution` queries in `database/contribution_database.py`.
- Storage: `storage/s3.py` stores audio at `recordings/{edition_id}/{recording_id}.{format}`, derived from recording metadata. Audio is never replaced under the same recording ID.
- Cues are stored as `(:Span)-[:SPAN_OF]->(:Cue {start_ms, end_ms})-[:CUE_OF]->(:Recording)`. `Cue` has no `id`, because cues are only written and read in bulk.
- `database/span_database.py` adjusts cue spans with the annotation rules, and deletes a `Cue` once its span is deleted.

## Upload lifecycle

Uploads use the multipart upload's spooled file rather than a second full in-memory copy. Metadata is published only after storage succeeds. There is no upload journal, cleanup queue, or client revision/operation identifier. All unreferenced storage objects are retained.
