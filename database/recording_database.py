from typing import TYPE_CHECKING, LiteralString

from exceptions import DataNotFoundError
from models.enums import AudioFormat
from models.recording import RecordingCue, RecordingInput, RecordingOutput, RecordingPatch

from .contribution_database import ContributionDatabase, contributions_return
from .database_validator import DatabaseValidator
from .nomen_database import NomenDatabase

if TYPE_CHECKING:
    from neo4j import AsyncManagedTransaction, AsyncSession

    from .database import Database

RECORDING_LABEL: LiteralString = "Recording"


class RecordingDatabase:
    _RECORDING_RETURN: LiteralString = f"""
    RETURN {{
        id: r.id, format: r.format, size_bytes: r.size_bytes, duration_ms: r.duration_ms, date: r.date,
        edition_id: m.id,
        text_id: e.id,
        license: coalesce([(r)-[:HAS_LICENSE]->(license:LicenseType) | license.name][0], "public"),
        language: [(r)-[lang_rel:HAS_LANGUAGE]->(lang:Language) | coalesce(lang_rel.bcp47, lang.code)][0],
        title: CASE WHEN EXISTS {{
            (r)-[:HAS_TITLE]->(:Nomen)-[:HAS_LOCALIZATION]->(:LocalizedText)
        }} THEN apoc.map.fromPairs([(r)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->
            (lt:LocalizedText)-[title_rel:HAS_LANGUAGE]->(title_lang:Language) |
            [coalesce(title_rel.bcp47, title_lang.code), lt.text]]) ELSE null END,
        contributions: {contributions_return("r")}
    }} AS recording
    """

    GET_BY_ID_QUERY: LiteralString = f"""
    MATCH (r:Recording {{id: $recording_id}})-[:RECORDING_OF]->(m:Edition)-[:EDITION_OF]->(e:Text)
    {_RECORDING_RETURN}
    """

    GET_BY_EDITION_ID_QUERY: LiteralString = f"""
    MATCH (r:Recording)-[:RECORDING_OF]->(m:Edition {{id: $edition_id}})-[:EDITION_OF]->(e:Text)
    WITH r, m, e
    ORDER BY r.id
    {_RECORDING_RETURN}
    """

    CREATE_QUERY: LiteralString = """
    MATCH (m:Edition {id: $edition_id})
    MATCH (license:LicenseType {name: $license})
    OPTIONAL MATCH (title:Nomen {id: $title_nomen_id})
    CREATE (r:Recording {
        id: $recording_id, format: $format, size_bytes: $size_bytes, duration_ms: $duration_ms, date: $date
    })
    WITH r, m, license, title
    CREATE (r)-[:RECORDING_OF]->(m), (r)-[:HAS_LICENSE]->(license)
    CALL (*) { WHEN title IS NOT NULL THEN { CREATE (r)-[:HAS_TITLE]->(title) } }
    CALL (*) {
        WHEN $language_code IS NOT NULL THEN {
            MATCH (lang:Language {code: $language_code})
            CREATE (r)-[:HAS_LANGUAGE {bcp47: $language}]->(lang)
        }
    }
    RETURN r.id AS recording_id
    """

    UPDATE_PROPERTIES_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})
    SET r += $properties
    FINISH
    """

    UPDATE_LICENSE_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})
    OPTIONAL MATCH (r)-[existing:HAS_LICENSE]->()
    DELETE existing
    WITH r
    MATCH (license:LicenseType {name: $license})
    MERGE (r)-[:HAS_LICENSE]->(license)
    FINISH
    """

    UPDATE_LANGUAGE_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})
    OPTIONAL MATCH (r)-[existing:HAS_LANGUAGE]->()
    DELETE existing
    WITH r
    MATCH (lang:Language {code: $language_code})
    MERGE (r)-[:HAS_LANGUAGE {bcp47: $language}]->(lang)
    FINISH
    """

    DELETE_TITLE_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})-[:HAS_TITLE]->(n:Nomen)
    OPTIONAL MATCH (n)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    DETACH DELETE n, lt
    FINISH
    """

    LINK_TITLE_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})
    MATCH (n:Nomen {id: $nomen_id})
    CREATE (r)-[:HAS_TITLE]->(n)
    FINISH
    """

    GET_STORAGE_KEY_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})-[:RECORDING_OF]->(m:Edition)
    RETURN m.id AS edition_id, r.format AS format
    """

    _DELETE_CUES_OF_MATCHED: LiteralString = """
    CALL (r) {
        MATCH (t:Cue)-[:CUE_OF]->(r)
        OPTIONAL MATCH (span:Span)-[:SPAN_OF]->(t)
        DETACH DELETE span, t
    }
    """

    _DELETE_MATCHED: LiteralString = f"""
    {_DELETE_CUES_OF_MATCHED}
    OPTIONAL MATCH (r)-[:HAS_CONTRIBUTION]->(c:Contribution)
    OPTIONAL MATCH (r)-[:HAS_TITLE]->(n:Nomen)-[:HAS_LOCALIZATION]->(lt:LocalizedText)
    DETACH DELETE r, c, n, lt
    FINISH
    """

    DELETE_QUERY: LiteralString = f"""
    MATCH (r:Recording {{id: $recording_id}})
    {_DELETE_MATCHED}
    """

    DELETE_ALL_QUERY: LiteralString = f"""
    MATCH (r:Recording)-[:RECORDING_OF]->(:Edition {{id: $edition_id}})
    {_DELETE_MATCHED}
    """

    GET_CUES_QUERY: LiteralString = """
    MATCH (span:Span)-[:SPAN_OF]->(t:Cue)-[:CUE_OF]->(:Recording {id: $recording_id})
    RETURN {start: span.start, end: span.end} AS span, t.start_ms AS start_ms, t.end_ms AS end_ms
    ORDER BY t.start_ms
    """

    CREATE_CUES_QUERY: LiteralString = """
    MATCH (r:Recording {id: $recording_id})
    UNWIND $cues AS cue
    CREATE (:Span {start: cue.span.start, end: cue.span.end})
        -[:SPAN_OF]->(:Cue {start_ms: cue.start_ms, end_ms: cue.end_ms})
        -[:CUE_OF]->(r)
    FINISH
    """

    DELETE_CUES_QUERY: LiteralString = f"""
    MATCH (r:Recording {{id: $recording_id}})
    {_DELETE_CUES_OF_MATCHED}
    FINISH
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def session(self) -> AsyncSession:
        return self._db.get_session()

    async def get(self, recording_id: str) -> RecordingOutput:
        async def read(tx: AsyncManagedTransaction) -> RecordingOutput:
            result = await tx.run(RecordingDatabase.GET_BY_ID_QUERY, recording_id=recording_id)
            record = await result.single()
            if record is None:
                raise DataNotFoundError(f"Recording with ID '{recording_id}' not found")
            return RecordingOutput.model_validate(record["recording"])

        async with self.session as session:
            return await session.execute_read(read)

    async def get_all(self, edition_id: str) -> list[RecordingOutput]:
        async def read(tx: AsyncManagedTransaction) -> list[RecordingOutput]:
            await DatabaseValidator.validate_edition_exists(tx, edition_id)
            result = await tx.run(RecordingDatabase.GET_BY_EDITION_ID_QUERY, edition_id=edition_id)
            return [RecordingOutput.model_validate(record["recording"]) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def get_storage_location(self, recording_id: str) -> tuple[str, AudioFormat]:
        """Return the edition ID and format needed to build the recording's storage key."""
        async with self.session as session:
            return await session.execute_read(
                lambda tx: RecordingDatabase._get_storage_location_with_transaction(tx, recording_id)
            )

    @staticmethod
    async def _get_storage_location_with_transaction(
        tx: AsyncManagedTransaction, recording_id: str
    ) -> tuple[str, AudioFormat]:
        result = await tx.run(RecordingDatabase.GET_STORAGE_KEY_QUERY, recording_id=recording_id)
        record = await result.single()
        if record is None:
            raise DataNotFoundError(f"Recording with ID '{recording_id}' not found")
        return record["edition_id"], AudioFormat(record["format"])

    async def get_cues(self, recording_id: str) -> list[RecordingCue]:
        async def read(tx: AsyncManagedTransaction) -> list[RecordingCue]:
            await RecordingDatabase._get_storage_location_with_transaction(tx, recording_id)
            result = await tx.run(RecordingDatabase.GET_CUES_QUERY, recording_id=recording_id)
            return [RecordingCue.model_validate(record) for record in await result.data()]

        async with self.session as session:
            return await session.execute_read(read)

    async def replace_cues(self, recording_id: str, cues: list[RecordingCue]) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            edition_id, _ = await RecordingDatabase._get_storage_location_with_transaction(tx, recording_id)
            max_end = max(cue.span.end for cue in cues)
            await DatabaseValidator.validate_edition_spans(tx, edition_id, max_end)
            await tx.run(RecordingDatabase.DELETE_CUES_QUERY, recording_id=recording_id)
            await tx.run(
                RecordingDatabase.CREATE_CUES_QUERY,
                recording_id=recording_id,
                cues=[cue.model_dump() for cue in cues],
            )

        async with self.session as session:
            await session.execute_write(write)

    async def delete_cues(self, recording_id: str) -> None:
        async def write(tx: AsyncManagedTransaction) -> None:
            await RecordingDatabase._get_storage_location_with_transaction(tx, recording_id)
            await tx.run(RecordingDatabase.DELETE_CUES_QUERY, recording_id=recording_id)

        async with self.session as session:
            await session.execute_write(write)

    async def add(
        self,
        edition_id: str,
        recording: RecordingInput,
        recording_id: str,
        audio_format: AudioFormat,
        size_bytes: int,
    ) -> str:
        async with self.session as session:
            return await session.execute_write(
                lambda tx: RecordingDatabase.add_with_transaction(
                    tx, edition_id, recording, recording_id, audio_format, size_bytes
                )
            )

    @staticmethod
    async def add_with_transaction(
        tx: AsyncManagedTransaction,
        edition_id: str,
        recording: RecordingInput,
        recording_id: str,
        audio_format: AudioFormat,
        size_bytes: int,
    ) -> str:
        await DatabaseValidator.validate_edition_exists(tx, edition_id)
        await DatabaseValidator.validate_contribution_references(tx, recording.contributions)

        title_nomen_id = None
        if recording.title:
            title_nomen_id = await NomenDatabase.create_with_transaction(tx, recording.title.root)

        language_code = None
        if recording.language:
            language_code = recording.language.split("-")[0].lower()
            await DatabaseValidator.validate_language_code_exists(tx, language_code)

        result = await tx.run(
            RecordingDatabase.CREATE_QUERY,
            recording_id=recording_id,
            edition_id=edition_id,
            format=audio_format.value,
            size_bytes=size_bytes,
            duration_ms=recording.duration_ms,
            date=recording.date,
            license=recording.license.value,
            title_nomen_id=title_nomen_id,
            language=recording.language,
            language_code=language_code,
        )

        record = await result.single(strict=True)
        created_recording_id = str(record["recording_id"])
        for contribution in recording.contributions:
            await ContributionDatabase.create_with_transaction(tx, RECORDING_LABEL, created_recording_id, contribution)

        return created_recording_id

    async def update(self, recording_id: str, patch: RecordingPatch) -> RecordingOutput:
        await self.get(recording_id)

        async def write(tx: AsyncManagedTransaction) -> None:
            properties = patch.model_dump(include={"date", "duration_ms"}, exclude_unset=True)
            if properties:
                await tx.run(
                    RecordingDatabase.UPDATE_PROPERTIES_QUERY,
                    recording_id=recording_id,
                    properties=properties,
                )

            if patch.license is not None:
                await tx.run(
                    RecordingDatabase.UPDATE_LICENSE_QUERY,
                    recording_id=recording_id,
                    license=patch.license.value,
                )

            if patch.language is not None:
                language_code = patch.language.split("-")[0].lower()
                await DatabaseValidator.validate_language_code_exists(tx, language_code)
                await tx.run(
                    RecordingDatabase.UPDATE_LANGUAGE_QUERY,
                    recording_id=recording_id,
                    language=patch.language,
                    language_code=language_code,
                )

            if patch.title is not None:
                await tx.run(RecordingDatabase.DELETE_TITLE_QUERY, recording_id=recording_id)
                nomen_id = await NomenDatabase.create_with_transaction(tx, patch.title.root)
                await tx.run(RecordingDatabase.LINK_TITLE_QUERY, recording_id=recording_id, nomen_id=nomen_id)

            if patch.contributions is not None:
                await DatabaseValidator.validate_contribution_references(tx, patch.contributions)
                await ContributionDatabase.delete_all_with_transaction(tx, RECORDING_LABEL, recording_id)
                for contribution in patch.contributions:
                    await ContributionDatabase.create_with_transaction(tx, RECORDING_LABEL, recording_id, contribution)

        async with self.session as session:
            await session.execute_write(write)

        return await self.get(recording_id)

    async def delete(self, recording_id: str) -> None:
        async with self.session as session:
            await session.execute_write(lambda tx: RecordingDatabase.delete_with_transaction(tx, recording_id))

    @staticmethod
    async def delete_with_transaction(tx: AsyncManagedTransaction, recording_id: str) -> None:
        await tx.run(RecordingDatabase.DELETE_QUERY, recording_id=recording_id)

    @staticmethod
    async def delete_all_with_transaction(tx: AsyncManagedTransaction, edition_id: str) -> None:
        await tx.run(RecordingDatabase.DELETE_ALL_QUERY, edition_id=edition_id)
