from typing import TYPE_CHECKING, Any, LiteralString

from exceptions import DataNotFoundError
from models.contribution import AIContribution, PersonContributionBase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction


def contributions_return(source: LiteralString) -> LiteralString:
    """Build the contributions list for a node's return map, given the variable holding that node.

    Every variable is prefixed so the fragment can be spliced into any query without shadowing its
    variables.
    """
    return f"""(
        [({source})-[:HAS_CONTRIBUTION]->(cn_c:Contribution)-[:BY]->(cn_person:Person) | {{
            type: "person",
            id: cn_person.id,
            bdrc_id: cn_person.bdrc,
            role: [(cn_c)-[:WITH_ROLE]->(cn_role:RoleType) | cn_role.name][0],
            name: CASE WHEN EXISTS {{
                (cn_person)-[:HAS_NAME]->(cn_check:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
                WHERE NOT EXISTS {{ (cn_check)-[:ALTERNATIVE_OF]->(:Nomen) }}
            }} THEN apoc.map.fromPairs([(cn_person)-[:HAS_NAME]->(cn_nomen:Nomen)-[:HAS_LOCALIZATION]->
                (cn_lt:LocalizedText)-[cn_rel:HAS_LANGUAGE]->(cn_lang:Language)
                WHERE NOT EXISTS {{ (cn_nomen)-[:ALTERNATIVE_OF]->(:Nomen) }} |
                [coalesce(cn_rel.bcp47, cn_lang.code), cn_lt.text]]) ELSE null END
        }}]
        +
        [({source})-[:HAS_CONTRIBUTION]->(cn_c:Contribution)-[:BY]->(cn_ai:AI) | {{
            type: "ai",
            id: cn_ai.id,
            role: [(cn_c)-[:WITH_ROLE]->(cn_role:RoleType) | cn_role.name][0]
        }}]
    )"""


class ContributionDatabase:
    """Create and delete Contribution subgraphs hanging off any node that has HAS_CONTRIBUTION.

    Source labels are trusted LiteralString constants, keeping unique-index seeks explicit.
    Neo4j 2025.11+ supports property indexes with dynamic labels, subject to planner limitations:
    https://neo4j.com/docs/cypher-manual/25/clauses/match/#dynamic-match
    The reviewed 2026.07.1 plan still used DynamicLabelNodeLookup for the dynamic equivalent.
    Retain static labels unless PROFILE on the deployed version shows comparable DB hits.
    """

    @staticmethod
    def create_person_query(label: LiteralString) -> LiteralString:
        return f"""
        MATCH (source:{label} {{id: $source_id}})
        MATCH (p:Person) WHERE (($person_id IS NOT NULL AND p.id = $person_id)
                                OR ($person_bdrc_id IS NOT NULL AND p.bdrc = $person_bdrc_id))
        MERGE (rt:RoleType {{name: $role_name}})
        CREATE (source)-[:HAS_CONTRIBUTION]->(c:Contribution)-[:BY]->(p),
               (c)-[:WITH_ROLE]->(rt)
        RETURN elementId(c) AS contribution_element_id
        """

    @staticmethod
    def create_ai_query(label: LiteralString) -> LiteralString:
        return f"""
        MATCH (source:{label} {{id: $source_id}})
        MERGE (rt:RoleType {{name: $role_name}})
        MERGE (ai:AI {{id: $ai_id}})
        CREATE (source)-[:HAS_CONTRIBUTION]->(c:Contribution)-[:BY]->(ai),
            (c)-[:WITH_ROLE]->(rt)
        RETURN elementId(c) AS contribution_element_id
        """

    @staticmethod
    def delete_all_query(label: LiteralString) -> LiteralString:
        return f"""
        MATCH (:{label} {{id: $source_id}})-[:HAS_CONTRIBUTION]->(c:Contribution)
        DETACH DELETE c
        FINISH
        """

    @staticmethod
    async def create_with_transaction(
        tx: AsyncManagedTransaction,
        source_label: LiteralString,
        source_id: str,
        contribution: PersonContributionBase | AIContribution,
    ) -> None:
        params: dict[str, Any] = {"source_id": source_id, "role_name": contribution.role.value}

        if isinstance(contribution, PersonContributionBase):
            query = ContributionDatabase.create_person_query(source_label)
            params |= {"person_id": contribution.id, "person_bdrc_id": contribution.bdrc_id}
            not_found = (
                f"Person or Role not found. Person: id={contribution.id}, "
                f"bdrc_id={contribution.bdrc_id}; Role: {contribution.role.value}"
            )
        else:
            query = ContributionDatabase.create_ai_query(source_label)
            params |= {"ai_id": contribution.id}
            not_found = f"AI contribution creation failed. AI: {contribution.id}; Role: {contribution.role.value}"

        result = await tx.run(query, params)
        if not await result.single():
            raise DataNotFoundError(not_found)

    @staticmethod
    async def delete_all_with_transaction(
        tx: AsyncManagedTransaction, source_label: LiteralString, source_id: str
    ) -> None:
        await tx.run(ContributionDatabase.delete_all_query(source_label), source_id=source_id)
