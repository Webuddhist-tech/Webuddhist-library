# Text Operations API Specification

This document specifies the operation-based text editing API for the OpenPecha backend, including the endpoint design, span adjustment algorithms, and edge case handling.

---

## Overview

The text operations API allows clients to modify edition content through atomic operations (insert, delete, replace) while automatically adjusting all associated span-based annotations.

### API Endpoint

```
PATCH /v2/editions/{edition_id}/content
```

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| **INSERT** | Insert text at a position | `position`, `text` |
| **DELETE** | Delete text in a range | `start`, `end` |
| **REPLACE** | Replace text in a range | `start`, `end`, `text` |

---

## Request Format

### Insert

```json
{
    "type": "insert",
    "position": 15,
    "text": "new text"
}
```

### Delete

```json
{
    "type": "delete",
    "start": 10,
    "end": 20
}
```

### Replace

```json
{
    "type": "replace",
    "start": 10,
    "end": 20,
    "text": "replacement text"
}
```

---

## Entity Types and Span Behavior

### Categories

| Entity Type | Category | Relationship Path |
|-------------|----------|-------------------|
| Segment | **Continuous** | `Segment → Segmentation → Edition` |
| Page | **Continuous** | `Page → Volume → Pagination → Edition` |
| Note | Annotation | `Note → Edition` |
| Mark | Annotation | `Mark → Edition` |
| BibliographicMetadata | Annotation | `BibMeta → Edition` |
| Attribute | Annotation | `Attribute → Edition` |
| TableOfContentsSection | Annotation | `TableOfContentsSection → TableOfContents → Edition` |

### Behavior Summary

| Operation | Segment | Page | Other annotations |
|-----------|---------|------|-------------------|
| Insert at start boundary | Shift† | Shift† | Shift |
| Insert at end boundary | Expand | Expand | Unchanged |
| Insert inside | Expand | Expand | Expand |
| Delete all entity text | Delete; retain existing markers | Reject the edit | Delete |
| Replace exact match | Preserve (resize) | Preserve (resize) | Delete |
| Replace across entities | One replacement owner; delete collapsed text lines | One replacement owner; reject if any page becomes empty | Delete fully covered annotations |

†Special case: Insert at position 0 on first span (start=0) **expands** instead of shifts.

---

## DELETE Behavior Matrix

| Overlap Type | Condition | Result |
|--------------|-----------|--------|
| **Before** | `del_end <= start` | Shift left by `del_len` |
| **After** | `del_start >= end` | Unchanged |
| **Fully encompasses** | `del_start <= start && del_end >= end` | **Delete** |
| **Overlaps start** | `del_start <= start < del_end < end` | `(del_start, end - del_len)` |
| **Overlaps end** | `start < del_start < end <= del_end` | `(start, del_start)` |
| **Inside span** | `start < del_start && del_end < end` | `(start, end - del_len)` |

---

## Replacement ownership

For each segmentation or pagination, replacement text belongs to the first fully covered nonempty entity, or the entity containing the replacement's start when none is fully covered. If a replacement starts before the annotated region and ends inside its first entity, that entity starts after the replacement text, preserving the original boundary behavior. All line boundaries use the same monotonic mapping. Neighboring segments therefore meet exactly, including when an edit spans several entities. Existing zero-width markers stay empty at a mapped boundary.

---

## Validation Rules

| Operation | Rule | Error |
|-----------|------|-------|
| INSERT | `position >= 0` | 400 Bad Request |
| INSERT | `position <= text_length` | 400 Bad Request |
| INSERT | `text` non-empty | 400 Bad Request |
| DELETE | `start >= 0` | 400 Bad Request |
| DELETE | `end <= text_length` | 400 Bad Request |
| DELETE | `start < end` | 400 Bad Request |
| REPLACE | Same as DELETE | 400 Bad Request |
| REPLACE | `text` required and non-empty | 400 Bad Request |

**Note**: Use DELETE for removing text. REPLACE with empty text is rejected.

Segmentation input must have contiguous lines within every segment and contiguous segments: each preceding end equals the next start. Gaps, overlaps, and reversed ordering are rejected with HTTP 422. This applies to both edition creation and standalone segmentation creation. Zero-width markers are allowed at boundaries; a segmentation may cover only part of the edition. Existing nonconforming segmentations must be corrected before migration.

Every pagination page must cover at least one character. Pages with no lines or only zero-width lines are rejected with HTTP 422. Content edits that would empty any page are rejected atomically, leaving content and annotations unchanged.

---

## Edge Cases

### 1. Insert at Position 0

**Problem**: If first segment starts at 0 and we insert at position 0, shifting would leave new content uncovered.

**Solution**: Special case - if `insert_pos == 0` and `start == 0`, expand instead of shift.

```
Before: S1 [0, 10), S2 [10, 20)
Insert "Hello " at position 0
After:  S1 [0, 16), S2 [16, 26)  ✓ (S1 expanded to cover new content)
```

### 2. Insert at Segment Boundary

**Problem**: Insert at exact boundary between adjacent segments could cause overlap.

**Solution**: Insert at `position == start` shifts the segment.

```
Before: S1 [0, 10), S2 [10, 20)
Insert "XX" at position 10
After:  S1 [0, 12), S2 [12, 22)  ✓ (S1 expanded at end, S2 shifted)
```

### 3. Replace Encompasses Multiple Segments

**Problem**: Multiple segments would all adjust to the same span, causing overlap.

**Solution**: Give the replacement to the first fully covered segment, trim adjacent segments to its boundaries, and delete other collapsed text lines. For pagination, reject the edit if it would empty any page.

```
Before: S1 [0, 10), S2 [10, 20), S3 [20, 30)
Replace [5, 25) with "XXX"
After:  S1 [0, 5), S2 [5, 8), S3 [8, 13)  ✓ (S2 kept, others adjusted)
```

### 4. Replace with Empty String

Use DELETE to remove text; REPLACE requires nonempty text. Zero-width spans are valid position markers. An edit that removes all edition content is rejected.

### 5. Delete Creates Gap in Segmentation

**Scenario**: Delete exactly matches a segment.

**Result**: Segment is deleted, adjacent segments shift to maintain continuity.

```
Before: S1 [0, 10), S2 [10, 20), S3 [20, 30)
Delete [10, 20)
After:  S1 [0, 10), S3 [10, 20)  ✓ (S2 deleted, S3 shifted)
```

### 6. Independent annotation collections

An edition's segmentation and pagination are adjusted independently.

```
Segmentation: [0, 20), [20, 40)
Pagination:   [0, 10), [10, 20), [20, 40)

Replace [5, 15) with "XXX" (delta = -7)

Segmentation: [0, 13), [13, 33)  ✓
Pagination:   [0, 8), [8, 13), [13, 33)  ✓
```
### Nonempty content and pages

Content edits must leave at least one character in the edition and on every page.
Pages are ordered by character offsets. Replacement text belongs to the first fully
covered page, or the page containing the replacement's start when none is fully covered. If that
would empty another page, the entire edit is rejected.

Zero-width annotation spans remain visible through single, list, and alignment
reads. A point lookup includes markers at that position and text spans containing
it; a range lookup includes markers at its start and excludes its end.
