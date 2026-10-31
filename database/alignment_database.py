from typing import TYPE_CHECKING, LiteralString

from database.content_state import read_value, touch_edition
from database.database_validator import DatabaseValidator
from exceptions import DataNotFoundError, InvalidRequestError
from models.alignment import EditionAlignmentInput, EditionAlignmentOutput, EditionAlignmentPairOutput

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction

    from .database import Database


class AlignmentDatabase:
    VALIDATE_EDITION_PAIR_QUERY: LiteralString = """
    RETURN EXISTS { (:Edition {id: $source_edition_id}) } AS source_edition_exists,
           EXISTS { (:Edition {id: $target_edition_id}) } AS target_edition_exists
    """

    VALIDATE_SEGMENT_REFERENCES_QUERY: LiteralString = """
    RETURN
      [reference IN $source_segment_references WHERE count {
        (:Edition {id: $source_edition_id})-[:HAS_SEGMENTATION]->(:Segmentation)
          <-[:SEGMENT_OF]-(:Segment {reference: reference})
      } <> 1] AS invalid_source_segment_references,
      [reference IN $target_segment_references WHERE count {
        (:Edition {id: $target_edition_id})-[:HAS_SEGMENTATION]->(:Segmentation)
          <-[:SEGMENT_OF]-(:Segment {reference: reference})
      } <> 1] AS invalid_target_segment_references
    """

    DELETE_EDITION_PAIR_QUERY: LiteralString = """
    MATCH (:Edition {id: $source_edition_id})-[:HAS_SEGMENTATION]->(:Segmentation)
      <-[:SEGMENT_OF]-(source_segment:Segment)
      -[relationship:ALIGNED_TO]->(target_segment:Segment)-[:SEGMENT_OF]->(:Segmentation)
      <-[:HAS_SEGMENTATION]-(:Edition {id: $target_edition_id})
    DELETE relationship
    RETURN count(relationship) AS count
    """

    CREATE_EDITION_PAIR_QUERY: LiteralString = """
    UNWIND $alignments AS alignment
    MATCH (:Edition {id: $source_edition_id})-[:HAS_SEGMENTATION]->(:Segmentation)
      <-[:SEGMENT_OF]-(source_segment:Segment {
        reference: alignment.source_segment_reference
      })
    MATCH (:Edition {id: $target_edition_id})-[:HAS_SEGMENTATION]->(:Segmentation)
      <-[:SEGMENT_OF]-(target_segment:Segment {
        reference: alignment.target_segment_reference
      })
    MERGE (source_segment)-[:ALIGNED_TO]->(target_segment)
    RETURN count(*) AS count
    """

    GET_EDITION_PAIR_QUERY: LiteralString = """
    MATCH (source_edition:Edition {id: $source_edition_id})-[:EDITION_OF]->(source_text:Text)
    MATCH (target_edition:Edition {id: $target_edition_id})-[:EDITION_OF]->(target_text:Text)
    MATCH (source_edition)-[:HAS_SEGMENTATION]->(source_segmentation:Segmentation)
      <-[:SEGMENT_OF]-(source_segment:Segment)-[:ALIGNED_TO]->(target_segment:Segment)
      -[:SEGMENT_OF]->(target_segmentation:Segmentation)<-[:HAS_SEGMENTATION]-(target_edition)
    CALL (source_segment) {
      MATCH (source_span:Span)-[:SPAN_OF]->(source_segment)

      WITH source_span ORDER BY source_span.start, source_span.end
      RETURN collect({start: source_span.start, end: source_span.end}) AS source_lines,
             min(source_span.start) AS source_min_start
    }
    CALL (target_segment) {
      MATCH (target_span:Span)-[:SPAN_OF]->(target_segment)

      WITH target_span ORDER BY target_span.start, target_span.end
      RETURN collect({start: target_span.start, end: target_span.end}) AS target_lines,
             min(target_span.start) AS target_min_start
    }
    WITH source_edition, source_text, source_segmentation, source_segment, source_lines, source_min_start,
         target_edition, target_text, target_segmentation, target_segment, target_lines, target_min_start
    WHERE size(source_lines) > 0 AND size(target_lines) > 0
    WITH source_edition, source_text, source_segmentation, source_segment, source_lines, source_min_start,
         target_edition, target_text, target_segmentation, target_segment, target_lines, target_min_start,
         [(source_segment)-[:HAS_TAG]->(source_tag:Tag)
            WHERE ($application IS NULL
              OR (source_tag)-[:BELONGS_TO]->(:Application {id: $application}))
            | source_tag.id] AS source_tag_ids,
         [(target_segment)-[:HAS_TAG]->(target_tag:Tag)
            WHERE ($application IS NULL
              OR (target_tag)-[:BELONGS_TO]->(:Application {id: $application}))
            | target_tag.id] AS target_tag_ids
    ORDER BY source_edition.id, source_segmentation.id, source_min_start, source_lines[-1].end, source_segment.id,
             target_edition.id, target_segmentation.id, target_min_start, target_lines[-1].end, target_segment.id
    SKIP $offset
    LIMIT $limit
    RETURN {
      id: source_segment.id,
      type: source_segment.type,
      reference: source_segment.reference,
      segmentation_id: source_segmentation.id,
      edition_id: source_edition.id,
      text_id: source_text.id,
      lines: source_lines,
      tag_ids: CASE WHEN size(source_tag_ids) = 0 THEN null ELSE source_tag_ids END
    } AS source_segment,
    {
      id: target_segment.id,
      type: target_segment.type,
      reference: target_segment.reference,
      segmentation_id: target_segmentation.id,
      edition_id: target_edition.id,
      text_id: target_text.id,
      lines: target_lines,
      tag_ids: CASE WHEN size(target_tag_ids) = 0 THEN null ELSE target_tag_ids END
    } AS target_segment
    """

    GET_BY_EDITION_QUERY: LiteralString = """
    MATCH (e:Edition {id: $edition_id})-[:EDITION_OF]->(et:Text)
    MATCH (e)-[:HAS_SEGMENTATION]->(:Segmentation)<-[:SEGMENT_OF]-(s:Segment)
          -[r:ALIGNED_TO]-(os:Segment)-[:SEGMENT_OF]->(:Segmentation)
          <-[:HAS_SEGMENTATION]-(o:Edition)-[:EDITION_OF]->(ot:Text)
    WITH e, et, o, ot, startNode(r) = s AS outgoing
    RETURN DISTINCT
      CASE WHEN outgoing THEN e.id ELSE o.id END AS aligned_edition_id,
      CASE WHEN outgoing THEN et.id ELSE ot.id END AS aligned_text_id,
      CASE WHEN outgoing THEN o.id ELSE e.id END AS target_edition_id,
      CASE WHEN outgoing THEN ot.id ELSE et.id END AS target_text_id
    ORDER BY aligned_text_id, aligned_edition_id, target_text_id, target_edition_id
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def replace(
        self,
        source_edition_id: str,
        target_edition_id: str,
        alignment: EditionAlignmentInput,
    ) -> None:
        async with self._db.get_session() as session:
            await session.execute_write(
                AlignmentDatabase.replace_with_transaction, source_edition_id, target_edition_id, alignment
            )

    async def get(
        self,
        source_edition_id: str,
        target_edition_id: str,
        *,
        offset: int,
        limit: int,
        application: str | None = None,
    ) -> list[EditionAlignmentPairOutput]:
        async with self._db.get_session() as session:
            return await session.execute_read(
                read_value,
                source_edition_id,
                lambda tx: AlignmentDatabase.get_with_transaction(
                    tx,
                    source_edition_id,
                    target_edition_id,
                    offset=offset,
                    limit=limit,
                    application=application,
                ),
            )

    async def get_all_for_edition(self, edition_id: str) -> list[EditionAlignmentOutput]:
        async with self._db.get_session() as session:
            return await session.execute_read(
                read_value,
                edition_id,
                lambda tx: AlignmentDatabase.get_all_for_edition_with_transaction(tx, edition_id),
            )

    async def delete(self, source_edition_id: str, target_edition_id: str) -> None:
        async with self._db.get_session() as session:
            await session.execute_write(AlignmentDatabase.delete_with_transaction, source_edition_id, target_edition_id)

    @staticmethod
    async def replace_with_transaction(
        tx: AsyncManagedTransaction,
        source_edition_id: str,
        target_edition_id: str,
        alignment: EditionAlignmentInput,
    ) -> None:
        for edition_id in sorted({source_edition_id, target_edition_id}):
            await touch_edition(tx, edition_id)
        await AlignmentDatabase._validate_segment_references(tx, source_edition_id, target_edition_id, alignment)
        await tx.run(
            AlignmentDatabase.DELETE_EDITION_PAIR_QUERY,
            source_edition_id=source_edition_id,
            target_edition_id=target_edition_id,
        )
        await tx.run(
            AlignmentDatabase.CREATE_EDITION_PAIR_QUERY,
            source_edition_id=source_edition_id,
            target_edition_id=target_edition_id,
            alignments=[item.model_dump() for item in alignment.alignments],
        )

    @staticmethod
    async def get_with_transaction(
        tx: AsyncManagedTransaction,
        source_edition_id: str,
        target_edition_id: str,
        *,
        offset: int,
        limit: int,
        application: str | None,
    ) -> list[EditionAlignmentPairOutput]:
        await AlignmentDatabase._validate_edition_pair(tx, source_edition_id, target_edition_id)
        result = await tx.run(
            AlignmentDatabase.GET_EDITION_PAIR_QUERY,
            source_edition_id=source_edition_id,
            target_edition_id=target_edition_id,
            offset=offset,
            limit=limit,
            application=application,
        )
        return [EditionAlignmentPairOutput.model_validate(record) for record in await result.data()]

    @staticmethod
    async def get_all_for_edition_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
    ) -> list[EditionAlignmentOutput]:
        await DatabaseValidator.validate_edition_exists(tx, edition_id)
        result = await tx.run(AlignmentDatabase.GET_BY_EDITION_QUERY, edition_id=edition_id)
        return [EditionAlignmentOutput.model_validate(record) for record in await result.data()]

    @staticmethod
    async def delete_with_transaction(
        tx: AsyncManagedTransaction,
        source_edition_id: str,
        target_edition_id: str,
    ) -> None:
        for edition_id in sorted({source_edition_id, target_edition_id}):
            await touch_edition(tx, edition_id)
        await tx.run(
            AlignmentDatabase.DELETE_EDITION_PAIR_QUERY,
            source_edition_id=source_edition_id,
            target_edition_id=target_edition_id,
        )

    @staticmethod
    async def _validate_edition_pair(
        tx: AsyncManagedTransaction,
        source_edition_id: str,
        target_edition_id: str,
    ) -> None:
        result = await tx.run(
            AlignmentDatabase.VALIDATE_EDITION_PAIR_QUERY,
            source_edition_id=source_edition_id,
            target_edition_id=target_edition_id,
        )
        record = await result.single()
        if not record or not record["source_edition_exists"]:
            raise DataNotFoundError(f"Source edition '{source_edition_id}' not found")
        if not record["target_edition_exists"]:
            raise DataNotFoundError(f"Target edition '{target_edition_id}' not found")

    @staticmethod
    async def _validate_segment_references(
        tx: AsyncManagedTransaction,
        source_edition_id: str,
        target_edition_id: str,
        alignment: EditionAlignmentInput,
    ) -> None:
        if source_edition_id == target_edition_id:
            self_aligned_references = sorted(
                {
                    item.source_segment_reference
                    for item in alignment.alignments
                    if item.source_segment_reference == item.target_segment_reference
                }
            )
            if self_aligned_references:
                raise InvalidRequestError(
                    "source_segment_reference and target_segment_reference must be different "
                    f"when aligning an edition to itself: {', '.join(self_aligned_references)}"
                )

        source_segment_references = sorted({item.source_segment_reference for item in alignment.alignments})
        target_segment_references = sorted({item.target_segment_reference for item in alignment.alignments})
        result = await tx.run(
            AlignmentDatabase.VALIDATE_SEGMENT_REFERENCES_QUERY,
            source_edition_id=source_edition_id,
            target_edition_id=target_edition_id,
            source_segment_references=source_segment_references,
            target_segment_references=target_segment_references,
        )
        record = await result.single()
        invalid_source_references = record["invalid_source_segment_references"] if record else source_segment_references
        invalid_target_references = record["invalid_target_segment_references"] if record else target_segment_references
        errors = []
        if invalid_source_references:
            errors.append(
                f"source segment references not found in edition '{source_edition_id}': "
                f"{', '.join(invalid_source_references)}"
            )
        if invalid_target_references:
            errors.append(
                f"target segment references not found in edition '{target_edition_id}': "
                f"{', '.join(invalid_target_references)}"
            )
        if errors:
            raise InvalidRequestError("; ".join(errors))
