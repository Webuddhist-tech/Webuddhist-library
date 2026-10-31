import logging

from neo4j import AsyncDriver, AsyncManagedTransaction

logger = logging.getLogger(__name__)

DATABASE_NAME = "neo4j"
SYSTEM_DATABASE_NAME = "system"

# Each trigger is a dict with:
#   name: unique trigger name
#   query: Cypher to execute on each transaction
#   phase: "before" or "after" commit
#   audit: standalone Cypher that checks the same invariant against ALL existing data
#
# Naming convention: enforce_{source_label}_{relationship}
# These cover every "required: true" relationship in neo4j_schema.yaml,
# plus target-type enforcement for optional typed relationships.


def _required_rel_trigger(
    name: str,
    description: str,
    source_label: str,
    rel_type: str,
    target_label: str,
    cardinality: str = "one",
) -> list[dict]:
    """Generate trigger + audit pairs for a required relationship.

    Returns a list of dicts:
      - Existence: a single trigger that fires on both node creation and
        relationship deletion, using CALL () { UNION ALL } to collect affected nodes.
      - If cardinality is 'one', also includes a uniqueness trigger (at most one).
    """
    triggers = [
        {
            "name": name,
            "description": description,
            "phase": "before",
            "query": f"""
                CALL () {{
                    UNWIND $createdNodes AS n
                    WITH n WHERE n:{source_label}
                    RETURN n AS node
                  UNION ALL
                    UNWIND $deletedRelationships AS rel
                    WITH rel WHERE type(rel) = '{rel_type}'
                    WITH startNode(rel) AS n
                    WHERE n:{source_label}
                    RETURN n AS node
                }}
                WITH DISTINCT node
                WHERE NOT (node IN $deletedNodes)
                  AND NOT (node)-[:{rel_type}]->(:{target_label})
                WITH collect(coalesce(node.id, elementId(node))) AS ids
                WHERE size(ids) > 0
                CALL apoc.util.validate(
                    true,
                    '{name}: {source_label} must have {rel_type}->{target_label}. IDs: %s',
                    [string.join(ids, ', ')]
                )
                RETURN null
            """,
            "audit": f"""
                MATCH (n:{source_label})
                WHERE NOT (n)-[:{rel_type}]->(:{target_label})
                RETURN coalesce(n.id, elementId(n)) AS violating_id
            """,
        },
    ]

    if cardinality == "one":
        cardinality_name = name + "_max_one"
        triggers.append(
            {
                "name": cardinality_name,
                "description": f"{source_label} must have at most one {rel_type}->{target_label}",
                "phase": "before",
                "query": f"""
                CALL () {{
                    UNWIND $createdNodes AS n
                    WITH n WHERE n:{source_label}
                    RETURN n AS node
                  UNION ALL
                    UNWIND $createdRelationships AS rel
                    WITH rel WHERE type(rel) = '{rel_type}'
                    WITH startNode(rel) AS n
                    WHERE n:{source_label}
                    RETURN n AS node
                }}
                WITH DISTINCT node
                WITH node, count {{ (node)-[:{rel_type}]->(:{target_label}) }} AS cnt
                WHERE cnt > 1
                WITH collect(coalesce(node.id, elementId(node))) AS ids
                WHERE size(ids) > 0
                CALL apoc.util.validate(
                    true,
                    '{cardinality_name}: {source_label} must have at most one {rel_type}. IDs: %s',
                    [string.join(ids, ', ')]
                )
                RETURN null
            """,
                "audit": f"""
                MATCH (n:{source_label})
                WITH n, count {{ (n)-[:{rel_type}]->(:{target_label}) }} AS cnt
                WHERE cnt > 1
                RETURN coalesce(n.id, elementId(n)) AS violating_id
            """,
            }
        )

    return triggers


def _rel_target_type_trigger(
    name: str,
    description: str,
    rel_type: str,
    target_label: str,
) -> dict:
    """Generate a trigger + audit pair that enforces a relationship's target node type."""
    return {
        "name": name,
        "description": description,
        "phase": "before",
        "query": f"""
            UNWIND $createdRelationships AS rel
            WITH rel
            WHERE type(rel) = '{rel_type}'
            WITH rel, startNode(rel) AS source, endNode(rel) AS target
            WHERE NOT target:{target_label}
            WITH collect(coalesce(source.id, elementId(source))) AS ids
            WHERE size(ids) > 0
            CALL apoc.util.validate(
                true,
                '{name}: {rel_type} must point to {target_label}. IDs: %s',
                [string.join(ids, ', ')]
            )
            RETURN null
        """,
        "audit": f"""
            MATCH (n)-[:{rel_type}]->(target)
            WHERE NOT target:{target_label}
            RETURN coalesce(n.id, elementId(n)) AS violating_id
        """,
    }


TRIGGERS: list[dict] = []

# Required outgoing relationships; the last field controls maximum cardinality.
for name, source, relationship, target, cardinality in [
    ("enforce_work_has_category", "Work", "HAS_CATEGORY", "Category", "one"),
    ("enforce_tag_belongs_to", "Tag", "BELONGS_TO", "Application", "one"),
    ("enforce_tag_has_title", "Tag", "HAS_TITLE", "Nomen", "one"),
    ("enforce_category_belongs_to", "Category", "BELONGS_TO", "Application", "one"),
    ("enforce_category_has_title", "Category", "HAS_TITLE", "Nomen", "one"),
    ("enforce_text_has_text_of", "Text", "TEXT_OF", "Work", "one"),
    ("enforce_text_has_language", "Text", "HAS_LANGUAGE", "Language", "one"),
    ("enforce_text_has_title", "Text", "HAS_TITLE", "Nomen", "one"),
    ("enforce_text_has_license", "Text", "HAS_LICENSE", "LicenseType", "one"),
    ("enforce_edition_has_type", "Edition", "HAS_TYPE", "EditionType", "one"),
    ("enforce_edition_edition_of", "Edition", "EDITION_OF", "Text", "one"),
    ("enforce_segment_segment_of", "Segment", "SEGMENT_OF", "Segmentation", "one"),
    ("enforce_bibmeta_bibliography_of", "BibliographicMetadata", "BIBLIOGRAPHY_OF", "Edition", "one"),
    ("enforce_bibmeta_has_type", "BibliographicMetadata", "HAS_TYPE", "BibliographyType", "one"),
    ("enforce_note_note_of", "Note", "NOTE_OF", "Edition", "one"),
    ("enforce_note_has_type", "Note", "HAS_TYPE", "NoteType", "one"),
    ("enforce_mark_mark_of", "Mark", "MARK_OF", "Edition", "one"),
    ("enforce_mark_has_type", "Mark", "HAS_TYPE", "MarkType", "one"),
    ("enforce_attribute_attribute_of", "Attribute", "ATTRIBUTE_OF", "Edition", "one"),
    ("enforce_attribute_has_type", "Attribute", "HAS_TYPE", "AttributeType", "one"),
    ("enforce_pagination_pagination_of", "Pagination", "PAGINATION_OF", "Edition", "one"),
    ("enforce_volume_volume_of", "Volume", "VOLUME_OF", "Pagination", "one"),
    ("enforce_page_page_of", "Page", "PAGE_OF", "Volume", "one"),
    ("enforce_recording_recording_of", "Recording", "RECORDING_OF", "Edition", "one"),
    ("enforce_recording_has_license", "Recording", "HAS_LICENSE", "LicenseType", "one"),
    ("enforce_recording_has_contribution", "Recording", "HAS_CONTRIBUTION", "Contribution", "many"),
    ("enforce_contribution_with_role", "Contribution", "WITH_ROLE", "RoleType", "one"),
    ("enforce_person_has_name", "Person", "HAS_NAME", "Nomen", "one"),
    ("enforce_nomen_has_localization", "Nomen", "HAS_LOCALIZATION", "LocalizedText", "many"),
    ("enforce_localizedtext_has_language", "LocalizedText", "HAS_LANGUAGE", "Language", "one"),
]:
    TRIGGERS.extend(
        _required_rel_trigger(
            name, f"{source} requires {relationship}->{target}", source, relationship, target, cardinality
        )
    )


# Text — target type enforcement for optional typed rels
TRIGGERS.append(
    _rel_target_type_trigger(
        "enforce_translation_of_target",
        "TRANSLATION_OF must point to a Text node",
        "TRANSLATION_OF",
        "Text",
    )
)
TRIGGERS.append(
    _rel_target_type_trigger(
        "enforce_commentary_of_target",
        "COMMENTARY_OF must point to a Text node",
        "COMMENTARY_OF",
        "Text",
    )
)

TRIGGERS.append(
    _rel_target_type_trigger(
        "enforce_edition_has_segmentation_target",
        "HAS_SEGMENTATION must point to a Segmentation node",
        "HAS_SEGMENTATION",
        "Segmentation",
    )
)
TRIGGERS.append(
    {
        "name": "enforce_edition_has_segmentation_max_one",
        "description": "Edition must have at most one HAS_SEGMENTATION relationship",
        "phase": "before",
        "query": """
        CALL () {
            UNWIND $createdNodes AS n
            WITH n WHERE n:Edition
            RETURN n AS node
          UNION ALL
            UNWIND $createdRelationships AS rel
            WITH rel WHERE type(rel) = 'HAS_SEGMENTATION'
            WITH startNode(rel) AS n
            WHERE n:Edition
            RETURN n AS node
        }
        WITH DISTINCT node
        WITH node, count { (node)-[:HAS_SEGMENTATION]->(:Segmentation) } AS cnt
        WHERE cnt > 1
        WITH collect(coalesce(node.id, elementId(node))) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_edition_has_segmentation_max_one: Edition must have at most one segmentation. IDs: %s',
            [string.join(ids, ', ')]
        )
        RETURN null
        """,
        "audit": """
        MATCH (e:Edition)
        WITH e, count { (e)-[:HAS_SEGMENTATION]->(:Segmentation) } AS cnt
        WHERE cnt > 1
        RETURN coalesce(e.id, elementId(e)) AS violating_id
        """,
    }
)

# --- Diplomatic Edition must have exactly one Pagination ------------------
TRIGGERS.append(
    {
        "name": "enforce_diplomatic_edition_has_pagination",
        "description": "A diplomatic Edition must have exactly one Pagination",
        "phase": "before",
        "query": """
        CALL () {
            UNWIND $createdNodes AS n
            WITH n WHERE n:Edition
            RETURN n AS node
          UNION ALL
            UNWIND $deletedRelationships AS rel
            WITH rel WHERE type(rel) = 'PAGINATION_OF'
            WITH endNode(rel) AS n
            WHERE n:Edition
            RETURN n AS node
        }
        WITH DISTINCT node
        WHERE NOT (node IN $deletedNodes)
          AND (node)-[:HAS_TYPE]->(:EditionType {name: 'diplomatic'})
        WITH node, count { (node)<-[:PAGINATION_OF]-(:Pagination) } AS cnt
        WHERE cnt <> 1
        WITH collect(coalesce(node.id, elementId(node))) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_diplomatic_edition_has_pagination: must have exactly one Pagination. IDs: %s',
            [string.join(ids, ', ')]
        )
        RETURN null
    """,
        "audit": """
        MATCH (e:Edition)-[:HAS_TYPE]->(:EditionType {name: 'diplomatic'})
        WITH e, count { (e)<-[:PAGINATION_OF]-(:Pagination) } AS cnt
        WHERE cnt <> 1
        RETURN coalesce(e.id, elementId(e)) AS violating_id
    """,
    }
)

TRIGGERS.append(
    {
        "name": "enforce_segment_reference_unique_per_segmentation",
        "description": "Non-null Segment.reference values must be unique within a segmentation",
        "phase": "before",
        "query": """
        CALL () {
            UNWIND $createdNodes AS node
            WITH node WHERE node:Segment
            RETURN node
          UNION ALL
            UNWIND $createdRelationships AS rel
            WITH rel WHERE type(rel) = 'SEGMENT_OF'
            WITH startNode(rel) AS node
            WHERE node:Segment
            RETURN node
        }
        WITH DISTINCT node
        WHERE node.reference IS NOT NULL
        MATCH (node)-[:SEGMENT_OF]->(segmentation:Segmentation)<-[:SEGMENT_OF]-(other:Segment)
        WHERE other <> node AND other.reference = node.reference
        WITH collect(DISTINCT node.reference) AS refs
        WHERE size(refs) > 0
        CALL apoc.util.validate(
            true,
            'enforce_segment_reference_unique_per_segmentation: Duplicate segment references: %s',
            [string.join(refs, ', ')]
        )
        RETURN null
        """,
        "audit": """
        MATCH (segmentation:Segmentation)<-[:SEGMENT_OF]-(segment:Segment)
        WHERE segment.reference IS NOT NULL
        WITH segmentation, segment.reference AS reference, count(segment) AS cnt
        WHERE cnt > 1
        RETURN segmentation.id + ':' + reference AS violating_id
        """,
    }
)

TRIGGERS.append(
    {
        "name": "enforce_span_span_of",
        "description": (
            "Every Span must have SPAN_OF to one of: Segment, BibliographicMetadata, Note, Mark, Attribute, Page, "
            "TableOfContentsSection, Cue"
        ),
        "phase": "before",
        "query": """
        CALL () {
            UNWIND $createdNodes AS n
            WITH n WHERE n:Span
            RETURN n AS node
          UNION ALL
            UNWIND $deletedRelationships AS rel
            WITH rel WHERE type(rel) = 'SPAN_OF'
            WITH startNode(rel) AS n
            WHERE n:Span
            RETURN n AS node
        }
        WITH DISTINCT node
        WHERE NOT (node IN $deletedNodes)
          AND NOT (node)-[:SPAN_OF]->(
            :Segment|BibliographicMetadata|Note|Mark|Attribute|Page|TableOfContentsSection|Cue
        )
        WITH collect(coalesce(toString(node.start), 'unknown')) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_span_span_of: Span must have SPAN_OF. Count: %s',
            [toString(size(ids))]
        )
        RETURN null
    """,
        "audit": """
        MATCH (s:Span)
        WHERE NOT (s)-[:SPAN_OF]->(
            :Segment|BibliographicMetadata|Note|Mark|Attribute|Page|TableOfContentsSection|Cue
        )
        RETURN elementId(s) AS violating_id
    """,
    }
)
TRIGGERS.append(
    {
        "name": "enforce_span_span_of_max_one",
        "description": "Span must have at most one SPAN_OF relationship",
        "phase": "before",
        "query": """
        CALL () {
            UNWIND $createdNodes AS n
            WITH n WHERE n:Span
            RETURN n AS node
          UNION ALL
            UNWIND $createdRelationships AS rel
            WITH rel WHERE type(rel) = 'SPAN_OF'
            WITH startNode(rel) AS n
            WHERE n:Span
            RETURN n AS node
        }
        WITH DISTINCT node
        WITH node, count { (node)-[:SPAN_OF]->() } AS cnt
        WHERE cnt > 1
        WITH collect(coalesce(toString(node.start) + '-' + toString(node.end), 'unknown')) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_span_span_of_max_one: Span must have at most one SPAN_OF. Count: %s',
            [toString(size(ids))]
        )
        RETURN null
    """,
        "audit": """
        MATCH (s:Span)
        WITH s, count { (s)-[:SPAN_OF]->() } AS cnt
        WHERE cnt > 1
        RETURN elementId(s) AS violating_id
    """,
    }
)


TRIGGERS.append(
    {
        "name": "enforce_recording_contribution_narrator",
        "description": "Every Contribution on a Recording must have WITH_ROLE->RoleType {name: 'narrator'}",
        "phase": "before",
        "query": """
        CALL () {
            UNWIND $createdNodes AS n
            WITH n WHERE n:Recording
            RETURN n AS node
          UNION ALL
            UNWIND $createdNodes AS n
            WITH n WHERE n:Contribution
            MATCH (node:Recording)-[:HAS_CONTRIBUTION]->(n)
            RETURN node
          UNION ALL
            UNWIND $createdNodes AS n
            WITH n WHERE n:RoleType
            MATCH (node:Recording)-[:HAS_CONTRIBUTION]->(:Contribution)-[:WITH_ROLE]->(n)
            RETURN node
          UNION ALL
            UNWIND $createdRelationships AS rel
            WITH rel WHERE type(rel) = 'HAS_CONTRIBUTION'
            WITH startNode(rel) AS n
            WHERE n:Recording
            RETURN n AS node
        }
        WITH DISTINCT node
        WHERE NOT (node IN $deletedNodes)
          AND EXISTS {
            (node)-[:HAS_CONTRIBUTION]->(c:Contribution)-[:WITH_ROLE]->(rt:RoleType)
            WHERE rt.name IS NULL OR rt.name <> 'narrator'
          }
        WITH collect(coalesce(node.id, elementId(node))) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_recording_contribution_narrator: Recording contributions must have role narrator. IDs: %s',
            [string.join(ids, ', ')]
        )
        RETURN null
    """,
        "audit": """
        MATCH (r:Recording)-[:HAS_CONTRIBUTION]->(:Contribution)-[:WITH_ROLE]->(rt:RoleType)
        WHERE rt.name IS NULL OR rt.name <> 'narrator'
        RETURN coalesce(r.id, elementId(r)) AS violating_id
    """,
    }
)

TRIGGERS.extend(
    _required_rel_trigger(
        "enforce_cue_cue_of",
        "Every Cue must have CUE_OF->Recording",
        "Cue",
        "CUE_OF",
        "Recording",
    )
)
TRIGGERS.append(
    {
        "name": "enforce_cue_start_ms_lt_end_ms",
        "description": "Cue.start_ms must be less than Cue.end_ms",
        "phase": "before",
        "query": """
        UNWIND $createdNodes AS node
        WITH node
        WHERE node:Cue
        WITH node
        WHERE node.start_ms >= node.end_ms
        WITH collect(toString(node.start_ms) + '-' + toString(node.end_ms)) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_cue_start_ms_lt_end_ms: Cue.start_ms must be < Cue.end_ms. Cues: %s',
            [string.join(ids, ', ')]
        )
        RETURN null
    """,
        "audit": """
        MATCH (t:Cue)
        WHERE t.start_ms >= t.end_ms
        RETURN toString(t.start_ms) + '-' + toString(t.end_ms) AS violating_id
    """,
    }
)

TRIGGERS.append(
    {
        "name": "enforce_contribution_by",
        "description": "Every Contribution must have BY->Person or BY->AI",
        "phase": "before",
        "query": """
        CALL () {
            UNWIND $createdNodes AS n
            WITH n WHERE n:Contribution
            RETURN n AS node
          UNION ALL
            UNWIND $deletedRelationships AS rel
            WITH rel WHERE type(rel) = 'BY'
            WITH startNode(rel) AS n
            WHERE n:Contribution
            RETURN n AS node
        }
        WITH DISTINCT node
        WHERE NOT (node IN $deletedNodes)
          AND NOT (node)-[:BY]->(:Person|AI)
        WITH collect(elementId(node)) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_contribution_by: Contribution must have BY->Person|AI. Count: %s',
            [toString(size(ids))]
        )
        RETURN null
    """,
        "audit": """
        MATCH (c:Contribution)
        WHERE NOT (c)-[:BY]->(:Person|AI)
        RETURN elementId(c) AS violating_id
    """,
    }
)
TRIGGERS.append(
    {
        "name": "enforce_contribution_by_max_one",
        "description": "Contribution must have at most one BY relationship",
        "phase": "before",
        "query": """
        CALL () {
            UNWIND $createdNodes AS n
            WITH n WHERE n:Contribution
            RETURN n AS node
          UNION ALL
            UNWIND $createdRelationships AS rel
            WITH rel WHERE type(rel) = 'BY'
            WITH startNode(rel) AS n
            WHERE n:Contribution
            RETURN n AS node
        }
        WITH DISTINCT node
        WITH node, count { (node)-[:BY]->() } AS cnt
        WHERE cnt > 1
        WITH collect(elementId(node)) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_contribution_by_max_one: Contribution must have at most one BY. Count: %s',
            [toString(size(ids))]
        )
        RETURN null
    """,
        "audit": """
        MATCH (c:Contribution)
        WITH c, count { (c)-[:BY]->() } AS cnt
        WHERE cnt > 1
        RETURN elementId(c) AS violating_id
    """,
    }
)


# --- Text: TRANSLATION_OF and COMMENTARY_OF are mutually exclusive ----------
TRIGGERS.append(
    {
        "name": "enforce_text_translation_commentary_exclusive",
        "description": "A Text must not have both TRANSLATION_OF and COMMENTARY_OF",
        "phase": "before",
        "query": """
        UNWIND $createdNodes AS node
        WITH node
        WHERE node:Text
        WITH node
        WHERE (node)-[:TRANSLATION_OF]->() AND (node)-[:COMMENTARY_OF]->()
        WITH collect(coalesce(node.id, elementId(node))) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_text_translation_commentary_exclusive: Text has both TRANSLATION_OF and COMMENTARY_OF. IDs: %s',
            [string.join(ids, ', ')]
        )
        RETURN null
    """,
        "audit": """
        MATCH (t:Text)
        WHERE (t)-[:TRANSLATION_OF]->() AND (t)-[:COMMENTARY_OF]->()
        RETURN coalesce(t.id, elementId(t)) AS violating_id
    """,
    }
)

# --- Self-referential prevention -------------------------------------------
# A relationship must not point back to the same node it originates from.


def _no_self_ref_trigger(
    name: str,
    description: str,
    rel_type: str,
) -> dict:
    """Generate a trigger + audit pair preventing a relationship from pointing to itself."""
    return {
        "name": name,
        "description": description,
        "phase": "before",
        "query": f"""
            UNWIND $createdRelationships AS rel
            WITH rel
            WHERE type(rel) = '{rel_type}'
            WITH rel, startNode(rel) AS source, endNode(rel) AS target
            WHERE source = target
            WITH collect(coalesce(source.id, elementId(source))) AS ids
            WHERE size(ids) > 0
            CALL apoc.util.validate(
                true,
                '{name}: {rel_type} must not be self-referential. IDs: %s',
                [string.join(ids, ', ')]
            )
            RETURN null
        """,
        "audit": f"""
            MATCH (n)-[:{rel_type}]->(n)
            RETURN coalesce(n.id, elementId(n)) AS violating_id
        """,
    }


TRIGGERS.append(
    _no_self_ref_trigger(
        "enforce_no_self_translation_of",
        "TRANSLATION_OF must not point to self",
        "TRANSLATION_OF",
    )
)
TRIGGERS.append(
    _no_self_ref_trigger(
        "enforce_no_self_commentary_of",
        "COMMENTARY_OF must not point to self",
        "COMMENTARY_OF",
    )
)
TRIGGERS.append(
    _no_self_ref_trigger(
        "enforce_no_self_alternative_of",
        "ALTERNATIVE_OF must not point to self",
        "ALTERNATIVE_OF",
    )
)
TRIGGERS.append(
    _no_self_ref_trigger(
        "enforce_no_self_has_parent",
        "HAS_PARENT must not point to self",
        "HAS_PARENT",
    )
)
TRIGGERS.append(
    _no_self_ref_trigger(
        "enforce_no_self_aligned_to",
        "ALIGNED_TO must not point to self",
        "ALIGNED_TO",
    )
)

# --- Span: start must not be greater than end ------------------------------
TRIGGERS.append(
    {
        "name": "enforce_span_start_lte_end",
        "description": "Span.start must not be greater than Span.end",
        "phase": "before",
        "query": """
        UNWIND $createdNodes AS node
        WITH node
        WHERE node:Span
        WITH node
        WHERE node.start IS NULL OR node.end IS NULL OR node.start < 0 OR node.start > node.end
        WITH collect(elementId(node)) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_span_start_lte_end: Span.start must be <= Span.end. Spans: %s',
            [string.join(ids, ', ')]
        )
        RETURN null
    """,
        "audit": """
        MATCH (s:Span)
        WHERE s.start IS NULL OR s.end IS NULL OR s.start < 0 OR s.start > s.end
        RETURN elementId(s) AS violating_id
    """,
    }
)

# --- Nomen: ALTERNATIVE_OF must not form chains ----------------------------
# If Nomen A -[:ALTERNATIVE_OF]-> Nomen B, then B must NOT itself have
# an outgoing ALTERNATIVE_OF. Only one level of indirection is allowed.
TRIGGERS.append(
    {
        "name": "enforce_nomen_no_alternative_chain",
        "description": "A Nomen that is ALTERNATIVE_OF another must not itself be the target of ALTERNATIVE_OF",
        "phase": "before",
        "query": """
        UNWIND $createdRelationships AS rel
        WITH rel
        WHERE type(rel) = 'ALTERNATIVE_OF'
        WITH startNode(rel) AS child, endNode(rel) AS parent
        WHERE (child)<-[:ALTERNATIVE_OF]-() OR (parent)-[:ALTERNATIVE_OF]->()
        WITH collect(coalesce(child.id, elementId(child))) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_nomen_no_alternative_chain: ALTERNATIVE_OF must not form chains. IDs: %s',
            [string.join(ids, ', ')]
        )
        RETURN null
    """,
        "audit": """
        MATCH (a:Nomen)-[:ALTERNATIVE_OF]->(b:Nomen)-[:ALTERNATIVE_OF]->(c:Nomen)
        RETURN coalesce(b.id, elementId(b)) AS violating_id
    """,
    }
)


# --- Work: at most one original Text --------------------------------------
# A Work can only have one Text with TEXT_OF {original: true}
TRIGGERS.append(
    {
        "name": "enforce_work_one_original_text",
        "description": "A Work can have at most one original Text (TEXT_OF with original:true)",
        "phase": "before",
        "query": """
        UNWIND $createdRelationships AS rel
        WITH rel
        WHERE type(rel) = 'TEXT_OF' AND rel.original = true
        WITH endNode(rel) AS work
        WHERE work:Work
        WITH work, count { (work)<-[:TEXT_OF {original: true}]-(:Text) } AS cnt
        WHERE cnt > 1
        WITH collect(coalesce(work.id, elementId(work))) AS ids
        WHERE size(ids) > 0
        CALL apoc.util.validate(
            true,
            'enforce_work_one_original_text: Work already has an original text. IDs: %s',
            [string.join(ids, ', ')]
        )
        RETURN null
        """,
        "audit": """
        MATCH (w:Work)<-[:TEXT_OF {original: true}]-(:Text)
        WITH w, count(*) AS cnt
        WHERE cnt > 1
        RETURN coalesce(w.id, elementId(w)) AS violating_id
        """,
    }
)

# --- Text: title + language must be unique --------------------------------
TRIGGERS.append(
    {
        "name": "enforce_text_title_unique",
        "description": "Text title + language combination must be unique",
        "phase": "before",
        "query": """
        UNWIND $createdNodes AS candidate
        WITH DISTINCT candidate WHERE NOT candidate IN $deletedNodes
        MATCH (candidate)-[:HAS_TITLE|HAS_LOCALIZATION*0..2]->(node:LocalizedText)
        WHERE EXISTS { (:Text)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(node) }
        MATCH (node)-[:HAS_LANGUAGE]->(language:Language)
        WITH DISTINCT node, language ORDER BY language.code
        WITH collect(DISTINCT node) AS titles, collect(DISTINCT language) AS languages
        // Serialize title checks on existing language nodes before reading competing titles.
        CALL apoc.lock.nodes(languages)
        UNWIND titles AS node
        MATCH (node)-[rel:HAS_LANGUAGE]->(lang:Language)
        WITH node, toLower(coalesce(rel.bcp47, lang.code)) AS title_lang
        MATCH (node)<-[:HAS_LOCALIZATION]-(:Nomen)<-[:HAS_TITLE]-(text_node:Text)
        MATCH (other:LocalizedText {text: node.text})-[other_rel:HAS_LANGUAGE]->(other_lang:Language)
        MATCH (other)<-[:HAS_LOCALIZATION]-(:Nomen)<-[:HAS_TITLE]-(other_text:Text)
        WHERE toLower(coalesce(other_rel.bcp47, other_lang.code)) = title_lang
        WITH node.text AS text, title_lang AS lang, count(DISTINCT other_text) AS text_count
        WHERE text_count > 1
        WITH collect(DISTINCT text + ':' + lang) AS combos
        WHERE size(combos) > 0
        CALL apoc.util.validate(
            true,
            'enforce_text_title_unique: Duplicate title+language exists. Combos: %s',
            [string.join(combos, ', ')]
        )
        RETURN null
        """,
        "audit": """
        MATCH (t:Text)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
              -[rel:HAS_LANGUAGE]->(l:Language)
        WITH lt.text AS text, toLower(coalesce(rel.bcp47, l.code)) AS lang, count(DISTINCT t) AS cnt
        WHERE cnt > 1
        RETURN text + ':' + lang AS violating_id
        """,
    }
)


for name, source, relationship, target in [
    ("enforce_toc_edition", "TableOfContents", "TOC_OF", "Edition"),
    ("enforce_toc_section", "TableOfContentsSection", "SECTION_OF", "TableOfContents"),
    ("enforce_toc_section_title", "TableOfContentsSection", "HAS_TITLE", "Nomen"),
]:
    TRIGGERS.extend(_required_rel_trigger(name, f"{source} requires {relationship}", source, relationship, target))

TRIGGERS.append(
    _no_self_ref_trigger("enforce_no_self_subsection_of", "Sections cannot parent themselves", "SUBSECTION_OF")
)
TRIGGERS.append(
    _rel_target_type_trigger("enforce_has_parent_target", "HAS_PARENT targets Category", "HAS_PARENT", "Category")
)
TRIGGERS.append(
    _rel_target_type_trigger(
        "enforce_subsection_target", "SUBSECTION_OF targets a section", "SUBSECTION_OF", "TableOfContentsSection"
    )
)

_TOC_INVALID = """
    count { (s)-[:SUBSECTION_OF]->() } > 1
    OR EXISTS { (s)-[:SUBSECTION_OF*1..]->(s) }
    OR EXISTS {
        (s)-[:SUBSECTION_OF]->(p:TableOfContentsSection)
        WHERE NOT EXISTS { (s)-[:SECTION_OF]->(:TableOfContents)<-[:SECTION_OF]-(p) }
    }
    OR EXISTS {
        (child:Span)-[:SPAN_OF]->(s)-[:SUBSECTION_OF]->(:TableOfContentsSection)<-[:SPAN_OF]-(parent:Span)
        WHERE child.start < parent.start OR child.end > parent.end
    }
"""
TRIGGERS.append(
    {
        "name": "enforce_toc_hierarchy",
        "description": "TOC parents form a contained tree within one TOC",
        "phase": "before",
        "query": f"""
        UNWIND $createdNodes AS n
        CALL (n) {{
            WITH n WHERE n:TableOfContentsSection RETURN n AS section
            UNION MATCH (n:Span)-[:SPAN_OF]->(section:TableOfContentsSection) RETURN section
        }}
        WITH section WHERE NOT section IN $deletedNodes
        UNWIND [section] + [(child:TableOfContentsSection)-[:SUBSECTION_OF]->(section) | child] AS s
        WITH DISTINCT s WHERE {_TOC_INVALID}
        WITH collect(coalesce(s.id, elementId(s))) AS ids WHERE size(ids) > 0
        CALL apoc.util.validate(
            true, 'enforce_toc_hierarchy: Invalid section hierarchy. IDs: %s', [string.join(ids, ', ')]
        )
        RETURN null
    """,
        "audit": f"""MATCH (s:TableOfContentsSection) WHERE {_TOC_INVALID}
            RETURN coalesce(s.id, elementId(s)) AS violating_id""",
    }
)

# APOC supplies changed properties and labels separately from created nodes.
# Expand that event set once when building the statements, so updates use the same rules.
_CHANGED_RELATIONSHIPS = """($createdRelationships
    + reduce(rels = [], key IN keys($assignedRelationshipProperties) |
        rels + [entry IN $assignedRelationshipProperties[key] | entry.relationship])
    + reduce(rels = [], key IN keys($removedRelationshipProperties) |
        rels + [entry IN $removedRelationshipProperties[key] | entry.relationship]))"""
_CHANGED_NODES = """($createdNodes
    + reduce(nodes = [], key IN keys($assignedNodeProperties) |
        nodes + [entry IN $assignedNodeProperties[key] | entry.node])
    + reduce(nodes = [], key IN keys($removedNodeProperties) |
        nodes + [entry IN $removedNodeProperties[key] | entry.node])
    + reduce(nodes = [], label IN keys($assignedLabels) | nodes + $assignedLabels[label])
    + reduce(nodes = [], label IN keys($removedLabels) | nodes + $removedLabels[label]
        + reduce(neighbors = [], node IN [n IN $removedLabels[label] WHERE NOT n IN $deletedNodes] |
            neighbors + [(node)--(neighbor) | neighbor]))
    + reduce(nodes = [], rel IN $createdRelationships + $deletedRelationships |
        nodes + [startNode(rel), endNode(rel)]))"""
_CHANGED_NODES = f"[node IN {_CHANGED_NODES} WHERE NOT node IN $deletedNodes]"
_CHANGED_RELATIONSHIPS = f"[rel IN {_CHANGED_RELATIONSHIPS} WHERE NOT rel IN $deletedRelationships]"
for trigger in TRIGGERS:
    trigger["query"] = (
        trigger["query"]
        .replace("$createdNodes", _CHANGED_NODES)
        .replace("$createdRelationships", _CHANGED_RELATIONSHIPS)
    )


async def install_triggers(driver: AsyncDriver, database: str = DATABASE_NAME) -> None:
    """Replace all installed triggers with TRIGGERS in one transaction. Idempotent — safe to call on every deploy."""

    async def write(tx: AsyncManagedTransaction) -> None:
        await tx.run("CALL apoc.trigger.dropAll($database)", database=database)

        for trigger in TRIGGERS:
            await tx.run(
                "CALL apoc.trigger.install($database, $name, $statement, {phase: $phase})",
                database=database,
                name=trigger["name"],
                statement=f"CYPHER 25 {trigger['query']}",
                phase=trigger["phase"],
            )
            logger.info("Installed trigger: %s — %s", trigger["name"], trigger["description"])

    async with driver.session(database=SYSTEM_DATABASE_NAME) as session:
        await session.execute_write(write)

    logger.info("All %d triggers installed.", len(TRIGGERS))


async def audit_triggers(driver: AsyncDriver, database: str = DATABASE_NAME) -> dict[str, list[str]]:
    """Run all audit queries against the full database. Returns a dict of trigger name -> list of violating IDs.

    An empty dict means no violations found.
    """

    async def read(tx: AsyncManagedTransaction) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for trigger in TRIGGERS:
            if "audit" not in trigger:
                continue

            result = await tx.run(trigger["audit"])
            records = await result.data()
            violating_ids = [str(r["violating_id"]) for r in records]
            if violating_ids:
                found[trigger["name"]] = violating_ids
                preview = ", ".join(violating_ids[:5])
                suffix = "…" if len(violating_ids) > 5 else ""
                logger.warning(
                    "Audit violation [%s]: %d nodes — %s%s",
                    trigger["name"],
                    len(violating_ids),
                    preview,
                    suffix,
                )
        return found

    async with driver.session(database=database) as session:
        violations = await session.execute_read(read)

    if not violations:
        logger.info("Audit passed: no violations found across %d constraints.", len(TRIGGERS))
    else:
        logger.warning("Audit found violations in %d constraint(s).", len(violations))

    return violations
