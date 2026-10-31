from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import aclosing
from typing import TYPE_CHECKING, Any

from database.database_validator import DatabaseValidator
from exceptions import DataNotFoundError
from models.contribution import PersonContributionOutput
from models.person import PersonOutput
from models.text import TextOutput
from search_client import SearchIndex

if TYPE_CHECKING:
    from opensearchpy import AsyncOpenSearch

    from database import Database
    from models.requests import PersonFilter, TextFilter

PERSON_DOCUMENT_TYPE = "person"
TEXT_DOCUMENT_TYPE = "text"

# The sanskrit/tibetan analyzers emit per-syllable tokens, so distinct names can
# share (or even reorder) the same syllables: "Śāntideva" -> [sa, nti, de, ba] and
# "Devaśānti" -> [de, ba, sa, nti] are anagrams. Person lookups therefore match the
# query as an ordered phrase, which keeps both syllable-neighbors and anagrams out.
# Titles are free-form multi-word phrases, so they instead allow ~25% of tokens to
# differ once past a 3-token query.
_TEXT_MINIMUM_SHOULD_MATCH = "3<75%"


class CatalogSearchService:
    def __init__(
        self,
        *,
        client: AsyncOpenSearch,
        index_name: str,
    ) -> None:
        self.index = SearchIndex(client, index_name)

    async def setup_index(self) -> None:
        await self.index.setup_index(_index_body())

    async def prepare_document(self, kind: str, entity_id: str, db: Database) -> dict:
        try:
            if kind == PERSON_DOCUMENT_TYPE:
                document = _person_document(await db.person.get(entity_id))
            else:
                document = _text_document(await db.text.get(entity_id))
        except DataNotFoundError:
            return {"id": _document_id(kind, entity_id), "document_type": kind, "deleted": True}
        return document

    async def search_persons(
        self, *, db: Database, query: str, filters: PersonFilter, offset: int, limit: int
    ) -> list[PersonOutput]:
        return await self._search(
            query, PERSON_DOCUMENT_TYPE, _person_filters(filters), offset, limit, db.person.get_by_ids
        )

    async def search_texts(
        self,
        *,
        db: Database,
        query: str,
        filters: TextFilter,
        offset: int,
        limit: int,
        application: str | None = None,
    ) -> list[TextOutput]:
        if filters.language:
            async with db.get_session() as session:
                await session.execute_read(
                    DatabaseValidator.validate_language_code_exists, filters.language.split("-")[0]
                )
        return await self._search(
            query,
            TEXT_DOCUMENT_TYPE,
            _text_filters(filters),
            offset,
            limit,
            lambda ids: db.text.get_by_ids(ids, application=application),
        )

    async def _search[T: (PersonOutput, TextOutput)](
        self,
        query: str,
        kind: str,
        filters: list[dict],
        offset: int,
        limit: int,
        hydrate: Callable[[list[str]], Awaitable[list[T]]],
    ) -> list[T]:
        body = _search_body(query=query, document_type=kind, limit=100, filters=filters)
        items: list[T] = []
        skipped = scanned = 0

        async with aclosing(self.index.pages(body)) as pages:
            async for hits in pages:
                scanned += len(hits)
                ids = [hit["_source"][f"{kind}_id"] for hit in hits]
                for item in await hydrate(ids):
                    if skipped < offset:
                        skipped += 1
                    else:
                        items.append(item)
                        if len(items) == limit:
                            return items
                if scanned >= 10000:
                    return items
        return items


def _index_body() -> dict:
    return {
        "settings": {
            "number_of_shards": 1,
            "analysis": {
                "analyzer": {
                    "catalog_folded": {
                        "type": "custom",
                        "tokenizer": "icu_tokenizer",
                        "filter": ["lowercase", "icu_folding"],
                    },
                    "catalog_plain": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase"],
                    },
                    "catalog_tibetan": {
                        "type": "custom",
                        "tokenizer": "tibetan",
                        "char_filter": ["catalog_tibetan_lenient_char"],
                        "filter": ["catalog_tibetan_lenient_filter"],
                    },
                    "catalog_tibetan_ewts": {
                        "type": "custom",
                        "tokenizer": "tibetan",
                        "char_filter": ["catalog_tibetan_ewts_char"],
                        "filter": ["catalog_tibetan_lenient_filter"],
                    },
                    "catalog_tibetan_phonetic_index": {
                        "type": "custom",
                        "tokenizer": "tibetan",
                        "char_filter": ["catalog_tibetan_lenient_char"],
                        "filter": ["tibetan-for-english-phonetic"],
                    },
                    "catalog_tibetan_ewts_phonetic_index": {
                        "type": "custom",
                        "tokenizer": "tibetan",
                        "char_filter": ["catalog_tibetan_ewts_char"],
                        "filter": ["tibetan-for-english-phonetic"],
                    },
                    "catalog_tibetan_phonetic_search": {
                        "type": "custom",
                        "tokenizer": "tibetan-english-phonetic",
                        "char_filter": ["tibetan-english-phonetic"],
                    },
                    "catalog_sanskrit_roman": {
                        "type": "custom",
                        "char_filter": ["catalog_sanskrit_roman_char"],
                        "tokenizer": "iast",
                        "filter": ["lowercase"],
                    },
                    "catalog_sanskrit_deva": {
                        "type": "custom",
                        "char_filter": ["catalog_sanskrit_deva_char"],
                        "tokenizer": "iast",
                        "filter": ["lowercase"],
                    },
                },
                "char_filter": {
                    "catalog_tibetan_lenient_char": {
                        "type": "tibetan",
                        "lenient": True,
                    },
                    "catalog_tibetan_ewts_char": {
                        "type": "tibetan",
                        "lenient": True,
                        "input_method": "ewts",
                    },
                    "catalog_sanskrit_roman_char": {
                        "type": "iast",
                        "input_method": "roman",
                        "lenient": True,
                        "filter_geminates": True,
                        "normalize_anusvara": True,
                    },
                    "catalog_sanskrit_deva_char": {
                        "type": "iast",
                        "input_method": "deva",
                        "lenient": True,
                        "filter_geminates": True,
                        "normalize_anusvara": True,
                    },
                },
                "filter": {
                    "catalog_tibetan_lenient_filter": {
                        "type": "tibetan",
                        "remove_affixes": True,
                        "normalize_paba": True,
                    },
                },
            },
        },
        "mappings": {
            "_meta": {"projection_version": 2},
            "properties": {
                "id": {"type": "keyword"},
                "document_type": {"type": "keyword"},
                "person_id": {"type": "keyword"},
                "text_id": {"type": "keyword"},
                "bdrc": {"type": "keyword"},
                "wiki": {"type": "keyword"},
                "language": {"type": "keyword"},
                "category_id": {"type": "keyword"},
                "tag_ids": {"type": "keyword"},
                "contributor_ids": {"type": "keyword"},
                "primary_labels": {"type": "text", "analyzer": "catalog_folded"},
                "alt_labels": {"type": "text", "analyzer": "catalog_folded"},
                "search_labels_folded": {"type": "text", "analyzer": "catalog_folded"},
                "search_labels_bo": {
                    "type": "text",
                    "analyzer": "catalog_tibetan",
                    "fields": {
                        "phonetic": {
                            "type": "text",
                            "analyzer": "catalog_tibetan_phonetic_index",
                            "search_analyzer": "catalog_tibetan_phonetic_search",
                        },
                        "ewts": {
                            "type": "text",
                            "analyzer": "catalog_tibetan",
                            "search_analyzer": "catalog_tibetan_ewts",
                        },
                    },
                },
                "search_labels_bo_ewts": {
                    "type": "text",
                    "analyzer": "catalog_tibetan_ewts",
                    "fields": {
                        "phonetic": {
                            "type": "text",
                            "analyzer": "catalog_tibetan_ewts_phonetic_index",
                            "search_analyzer": "catalog_tibetan_phonetic_search",
                        },
                    },
                },
                "search_labels_sa": {"type": "text", "analyzer": "catalog_sanskrit_roman"},
                "search_labels_sa_deva": {"type": "text", "analyzer": "catalog_sanskrit_deva"},
                "search_labels_plain": {"type": "text", "analyzer": "catalog_plain"},
            },
        },
    }


def _person_document(person: PersonOutput) -> dict:
    labels = _build_label_fields(person.name.root, [alt.root for alt in person.alt_names or []])
    return {
        "id": _document_id(PERSON_DOCUMENT_TYPE, person.id),
        "document_type": PERSON_DOCUMENT_TYPE,
        "person_id": person.id,
        "bdrc": person.bdrc,
        "wiki": person.wiki,
        **labels,
    }


def _text_document(text: TextOutput) -> dict:
    labels = _build_label_fields(text.title.root, [alt.root for alt in text.alt_titles or []])
    return {
        "id": _document_id(TEXT_DOCUMENT_TYPE, text.id),
        "document_type": TEXT_DOCUMENT_TYPE,
        "text_id": text.id,
        "bdrc": text.bdrc,
        "wiki": text.wiki,
        "language": text.language.lower(),
        "category_id": text.category_id,
        "tag_ids": text.tag_ids,
        "contributor_ids": _contributor_ids(text),
        **labels,
    }


def _build_label_fields(primary: dict[str, str], alternatives: list[dict[str, str]]) -> dict[str, list[str]]:
    primary_labels = list(primary.values())
    alt_labels = [label for alt in alternatives for label in alt.values()]

    localized_labels = list(primary.items())
    for alt in alternatives:
        localized_labels.extend(alt.items())

    fields: dict[str, list[str]] = {
        "primary_labels": primary_labels,
        "alt_labels": alt_labels,
        "search_labels_folded": primary_labels + alt_labels,
        "search_labels_bo": [],
        "search_labels_bo_ewts": [],
        "search_labels_sa": [],
        "search_labels_sa_deva": [],
        "search_labels_plain": [],
    }
    for lang, label in localized_labels:
        target = _search_field_for_language(lang)
        if target is not None:
            fields[target].append(label)

    return {key: _dedupe(value) for key, value in fields.items()}


def _search_field_for_language(language: str) -> str | None:
    normalized = language.casefold()
    base = normalized.split("-")[0]
    if base == "bo":
        if "ewts" in normalized or "wylie" in normalized:
            return "search_labels_bo_ewts"
        return "search_labels_bo"
    if base in {"sa", "pi"}:
        if "deva" in normalized or "devanagari" in normalized:
            return "search_labels_sa_deva"
        return "search_labels_sa"
    if base == "en":
        return "search_labels_plain"
    return "search_labels_plain" if _is_latinish(language) else None


def _is_latinish(language: str) -> bool:
    normalized = language.casefold()
    return any(token in normalized for token in ("latin", "iast", "roman", "alalc"))


def _contributor_ids(text: TextOutput) -> list[str]:
    return _dedupe(
        [
            contribution.id
            for contribution in text.contributions
            if isinstance(contribution, PersonContributionOutput) and contribution.id is not None
        ]
    )


def _search_body(*, query: str, document_type: str, limit: int, filters: list[dict]) -> dict:
    fields = _search_fields()
    return {
        "size": limit,
        "sort": [{"_score": "desc"}, {"id": "asc"}],
        "query": {
            "bool": {
                "filter": [{"term": {"document_type": document_type}}, *filters],
                "must": [
                    {
                        "bool": {
                            "should": _match_clauses(query=query, document_type=document_type, fields=fields),
                            "minimum_should_match": 1,
                        }
                    }
                ],
            }
        },
    }


def _match_clauses(*, query: str, document_type: str, fields: list[str]) -> list[dict]:
    phrase = {"multi_match": {"query": query, "type": "phrase", "fields": fields, "boost": 4}}
    if document_type == PERSON_DOCUMENT_TYPE:
        return [phrase]
    return [
        phrase,
        {
            "multi_match": {
                "query": query,
                "type": "best_fields",
                "fields": fields,
                "minimum_should_match": _TEXT_MINIMUM_SHOULD_MATCH,
            }
        },
    ]


def _search_fields() -> list[str]:
    return [
        "primary_labels^6",
        "alt_labels^3",
        "search_labels_bo^4",
        "search_labels_bo.phonetic^3",
        "search_labels_bo.ewts^3",
        "search_labels_bo_ewts^4",
        "search_labels_bo_ewts.phonetic^3",
        "search_labels_sa^4",
        "search_labels_sa_deva^3",
        "search_labels_plain^3",
        "search_labels_folded^2",
    ]


def _person_filters(filters: PersonFilter) -> list[dict]:
    return _optional_term_filters(
        {
            "bdrc": filters.bdrc,
            "wiki": filters.wiki,
        }
    )


def _text_filters(filters: TextFilter) -> list[dict]:
    filter_clauses = _optional_term_filters(
        {
            "category_id": filters.category_id,
            "bdrc": filters.bdrc,
            "wiki": filters.wiki,
        }
    )
    if language := filters.language:
        language_filter: dict = {"term": {"language": language}}
        if "-" not in language:
            language_filter = {"bool": {"should": [language_filter, {"prefix": {"language": language + "-"}}]}}
        filter_clauses.append(language_filter)
    tag_ids = filters.tag_ids
    if filters.tag_id_match == "all":
        filter_clauses.extend({"term": {"tag_ids": tag_id}} for tag_id in tag_ids)
    elif tag_ids:
        filter_clauses.append({"terms": {"tag_ids": tag_ids}})
    if filters.author_id:
        filter_clauses.append({"term": {"contributor_ids": filters.author_id}})
    return filter_clauses


def _optional_term_filters(values: dict[str, Any | None]) -> list[dict]:
    return [{"term": {field: value}} for field, value in values.items() if value is not None]


def _document_id(document_type: str, entity_id: str) -> str:
    return f"{document_type}:{entity_id}"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
