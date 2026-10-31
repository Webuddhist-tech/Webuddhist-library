# ruff: noqa: ANN001, S101, SLF001
import re
from dataclasses import dataclass

import pytest

from database.neo4j_triggers import DATABASE_NAME, SYSTEM_DATABASE_NAME, TRIGGERS, install_triggers
from neo4j.exceptions import ClientError
from tests.schema_contract import EXPECTED_TRIGGERS


@dataclass(frozen=True)
class TriggerCase:
    name: str
    setup_query: str


def _query_with_metadata(setup_query: str, trigger_query: str) -> str:
    return f"""
    CALL {{
        {setup_query}
    }}
    WITH createdNodes, createdRelationships, deletedRelationships, deletedNodes
    CALL apoc.cypher.doIt(
        $trigger_query,
        {{
            createdNodes: createdNodes,
            createdRelationships: createdRelationships,
            deletedRelationships: deletedRelationships,
            deletedNodes: deletedNodes,
            assignedNodeProperties: {{}}, removedNodeProperties: {{}},
            assignedRelationshipProperties: {{}}, removedRelationshipProperties: {{}},
            assignedLabels: {{}}, removedLabels: {{}}
        }}
    ) YIELD value
    RETURN value
    """


def _extract_required_existence_meta(query: str) -> tuple[str, str, str] | None:
    source = re.search(r"WITH n WHERE n:([A-Za-z][A-Za-z0-9_]*)", query)
    rel_target = re.search(r"AND NOT \(node\)-\[:([A-Z_]+)\]->\(:([A-Za-z0-9_]+)\)", query)
    if not source or not rel_target:
        return None
    return source.group(1), rel_target.group(1), rel_target.group(2)


def _extract_max_one_meta(query: str) -> tuple[str, str, str | None] | None:
    source = re.search(r"WITH n WHERE n:([A-Za-z][A-Za-z0-9_]*)", query)
    rel_target = re.search(r"count \{ \(node\)-\[:([A-Z_]+)\]->(?:\(:([A-Za-z0-9_]+)\)|\(\)) \}", query)
    if not source or not rel_target:
        return None
    return source.group(1), rel_target.group(1), rel_target.group(2)


def _extract_target_type_meta(query: str) -> tuple[str, str] | None:
    rel = re.search(r"WHERE type\(rel\) = '([A-Z_]+)'", query)
    target = re.search(r"WHERE NOT target:([A-Za-z][A-Za-z0-9_]*)", query)
    if not rel or not target:
        return None
    return rel.group(1), target.group(1)


def _extract_no_self_rel(query: str) -> str | None:
    rel = re.search(r"WHERE type\(rel\) = '([A-Z_]+)'", query)
    return rel.group(1) if rel else None


def _required_existence_case(source_label: str) -> str:
    return f"""
    CREATE (source:{source_label} {{id: randomUUID()}})
    RETURN [source] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
    """


def _required_max_one_case(source_label: str, rel_type: str, target_label: str | None) -> str:
    target_label = target_label or "GenericTarget"
    return f"""
    CREATE (source:{source_label} {{id: randomUUID()}})
    CREATE (target_1:{target_label} {{id: randomUUID()}})
    CREATE (target_2:{target_label} {{id: randomUUID()}})
    CREATE (source)-[rel_1:{rel_type}]->(target_1)
    CREATE (source)-[rel_2:{rel_type}]->(target_2)
    RETURN [source] AS createdNodes, [rel_1, rel_2] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
    """


def _target_type_case(rel_type: str, expected_target: str) -> str:
    wrong_label = "WrongTarget" if expected_target != "WrongTarget" else "DefinitelyWrongTarget"
    return f"""
    CREATE (source:SourceNode {{id: randomUUID()}})
    CREATE (target:{wrong_label} {{id: randomUUID()}})
    CREATE (source)-[rel:{rel_type}]->(target)
    RETURN [] AS createdNodes, [rel] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
    """


def _no_self_case(rel_type: str) -> str:
    return f"""
    CREATE (node:SelfRefNode {{id: randomUUID()}})
    CREATE (node)-[rel:{rel_type}]->(node)
    RETURN [] AS createdNodes, [rel] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
    """


def _custom_cases() -> dict[str, str]:
    return {
        "enforce_toc_hierarchy": """
            CREATE (a:TableOfContentsSection {id: randomUUID()})
            CREATE (b:TableOfContentsSection {id: randomUUID()})
            CREATE (a)-[:SUBSECTION_OF]->(b), (b)-[:SUBSECTION_OF]->(a)
            RETURN [a, b] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_edition_has_segmentation_max_one": """
            CREATE (edition:Edition {id: randomUUID()})
            CREATE (seg_1:Segmentation {id: randomUUID()})
            CREATE (seg_2:Segmentation {id: randomUUID()})
            CREATE (edition)-[rel_1:HAS_SEGMENTATION]->(seg_1)
            CREATE (edition)-[rel_2:HAS_SEGMENTATION]->(seg_2)
            RETURN [edition] AS createdNodes, [rel_1, rel_2] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_diplomatic_edition_has_pagination": """
            MERGE (etype:EditionType {name: 'diplomatic'})
            CREATE (edition:Edition {id: randomUUID()})
            CREATE (edition)-[:HAS_TYPE]->(etype)
            RETURN [edition] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_segment_reference_unique_per_segmentation": """
            CREATE (segmentation:Segmentation {id: randomUUID()})
            CREATE (:Segment {id: randomUUID(), reference: 'duplicate-ref'})-[:SEGMENT_OF]->(segmentation)
            CREATE (segment_2:Segment {id: randomUUID(), reference: 'duplicate-ref'})-[:SEGMENT_OF]->(segmentation)
            RETURN [segment_2] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_span_span_of": """
            CREATE (span:Span {start: 0, end: 1})
            RETURN [span] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_span_span_of_max_one": """
            CREATE (span:Span {start: 0, end: 1})
            CREATE (segmentation:Segmentation {id: randomUUID()})
            CREATE (segment:Segment {id: randomUUID()})-[:SEGMENT_OF]->(segmentation)
            CREATE (page:Page {id: randomUUID()})
            CREATE (span)-[rel_1:SPAN_OF]->(segment)
            CREATE (span)-[rel_2:SPAN_OF]->(page)
            RETURN [span] AS createdNodes, [rel_1, rel_2] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_recording_contribution_narrator": """
            MERGE (role:RoleType {name: 'translator'})
            CREATE (recording:Recording {id: randomUUID()})
            CREATE (recording)-[:HAS_CONTRIBUTION]->(:Contribution)-[:WITH_ROLE]->(role)
            RETURN [recording] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_contribution_by": """
            CREATE (contribution:Contribution {id: randomUUID()})
            RETURN [contribution] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_contribution_by_max_one": """
            CREATE (contribution:Contribution {id: randomUUID()})
            CREATE (person:Person {id: randomUUID()})
            CREATE (ai:AI {id: randomUUID()})
            CREATE (contribution)-[rel_1:BY]->(person)
            CREATE (contribution)-[rel_2:BY]->(ai)
            RETURN [contribution] AS createdNodes, [rel_1, rel_2] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_text_translation_commentary_exclusive": """
            CREATE (source:Text {id: randomUUID()})
            CREATE (target_translation:Text {id: randomUUID()})
            CREATE (target_commentary:Text {id: randomUUID()})
            CREATE (source)-[:TRANSLATION_OF]->(target_translation)
            CREATE (source)-[:COMMENTARY_OF]->(target_commentary)
            RETURN [source] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_span_start_lte_end": """
            CREATE (span:Span {start: 6, end: 5})
            RETURN [span] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_cue_start_ms_lt_end_ms": """
            CREATE (cue:Cue {start_ms: 500, end_ms: 500})
            RETURN [cue] AS createdNodes, [] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_nomen_no_alternative_chain": """
            CREATE (nomen_1:Nomen {id: randomUUID()})
            CREATE (nomen_2:Nomen {id: randomUUID()})
            CREATE (nomen_3:Nomen {id: randomUUID()})
            CREATE (nomen_1)-[rel_1:ALTERNATIVE_OF]->(nomen_2)
            CREATE (nomen_2)-[rel_2:ALTERNATIVE_OF]->(nomen_3)
            RETURN [] AS createdNodes, [rel_1, rel_2] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_work_one_original_text": """
            CREATE (work:Work {id: randomUUID()})
            CREATE (:Text {id: randomUUID()})-[rel_1:TEXT_OF {original: true}]->(work)
            CREATE (:Text {id: randomUUID()})-[rel_2:TEXT_OF {original: true}]->(work)
            RETURN [] AS createdNodes, [rel_1, rel_2] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
        "enforce_text_title_unique": """
            CREATE (lang:Language {code: 'test-lang-' + randomUUID()})
            CREATE (text_a:Text {id: randomUUID()})
            CREATE (text_b:Text {id: randomUUID()})
            CREATE (title_a:Nomen {id: randomUUID()})
            CREATE (title_b:Nomen {id: randomUUID()})
            CREATE (text_a)-[:HAS_TITLE]->(title_a)
            CREATE (text_b)-[:HAS_TITLE]->(title_b)
            CREATE (lt_a:LocalizedText {text: 'Duplicate trigger title'})
            CREATE (lt_b:LocalizedText {text: 'Duplicate trigger title'})
            CREATE (title_a)-[:HAS_LOCALIZATION]->(lt_a)-[:HAS_LANGUAGE {bcp47: 'bo'}]->(lang)
            CREATE (title_b)-[:HAS_LOCALIZATION]->(lt_b)-[rel:HAS_LANGUAGE {bcp47: 'bo'}]->(lang)
            RETURN [lt_b] AS createdNodes, [rel] AS createdRelationships, [] AS deletedRelationships, [] AS deletedNodes
        """,
    }


def _build_trigger_cases() -> list[TriggerCase]:
    custom = _custom_cases()
    cases: list[TriggerCase] = []

    for trigger in TRIGGERS:
        name = trigger["name"]
        query = trigger["query"]

        if name in custom:
            cases.append(TriggerCase(name=name, setup_query=custom[name]))
            continue

        target_type = _extract_target_type_meta(query)
        if target_type:
            rel_type, target_label = target_type
            cases.append(TriggerCase(name=name, setup_query=_target_type_case(rel_type, target_label)))
            continue

        if name.startswith("enforce_no_self_"):
            rel_type = _extract_no_self_rel(query)
            assert rel_type is not None, f"Could not parse self-reference relationship type for {name}"
            cases.append(TriggerCase(name=name, setup_query=_no_self_case(rel_type)))
            continue

        if name.endswith("_max_one"):
            max_meta = _extract_max_one_meta(query)
            assert max_meta is not None, f"Could not parse max-one trigger metadata for {name}"
            source_label, rel_type, target_label = max_meta
            cases.append(
                TriggerCase(
                    name=name,
                    setup_query=_required_max_one_case(source_label, rel_type, target_label),
                )
            )
            continue

        req_meta = _extract_required_existence_meta(query)
        assert req_meta is not None, f"Could not parse required-relationship trigger metadata for {name}"
        source_label, _, _ = req_meta
        cases.append(TriggerCase(name=name, setup_query=_required_existence_case(source_label)))

    expected = EXPECTED_TRIGGERS
    actual = {case.name for case in cases}
    assert expected == actual, f"Missing trigger cases: {sorted(expected - actual)}"
    return cases


TRIGGER_CASES = _build_trigger_cases()
TRIGGER_QUERY_BY_NAME = {trigger["name"]: trigger["query"] for trigger in TRIGGERS}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("case", TRIGGER_CASES, ids=lambda case: case.name)
async def test_each_trigger_rejects_intentional_invalid_data(test_database, case: TriggerCase) -> None:
    query = _query_with_metadata(case.setup_query, TRIGGER_QUERY_BY_NAME[case.name])
    async with test_database.get_session() as session:
        with pytest.raises(ClientError, match=case.name):
            result = await session.run(query, trigger_query=TRIGGER_QUERY_BY_NAME[case.name])
            await result.consume()


@pytest.mark.asyncio(loop_scope="session")
async def test_install_triggers_replaces_stale_triggers(test_database) -> None:
    driver = test_database._driver
    async with driver.session(database=SYSTEM_DATABASE_NAME) as session:
        result = await session.run(
            "CALL apoc.trigger.install($database, 'stale_trigger', 'RETURN 1', {phase: 'before'})",
            database=DATABASE_NAME,
        )
        await result.consume()

    await install_triggers(driver)

    async with driver.session(database=SYSTEM_DATABASE_NAME) as session:
        result = await session.run("CALL apoc.trigger.show($database) YIELD name, query", database=DATABASE_NAME)
        stored = {record["name"]: record["query"] async for record in result}

    assert set(stored) == {trigger["name"] for trigger in TRIGGERS}
    assert all(query.startswith("CYPHER 25 ") for query in stored.values())

