from datetime import datetime
from typing import Self

from .base import OpenPechaModel


class IdResponse(OpenPechaModel):
    id: str


class IdsResponse(OpenPechaModel):
    ids: list[str]


class LanguageResponse(OpenPechaModel):
    code: str
    name: str


class TextLanguageResponse(LanguageResponse):
    text_count: int


class ApplicationResponse(OpenPechaModel):
    id: str
    name: str


class PresignedUrlResponse(OpenPechaModel):
    url: str
    expires_at: datetime


class PaginatedResponse[T](OpenPechaModel):
    items: list[T]
    has_more: bool
    offset: int
    limit: int

    @classmethod
    def from_items(cls, items: list[T], *, offset: int, limit: int) -> Self:
        page_items = items[:limit]
        return cls(items=page_items, has_more=len(items) > limit, offset=offset, limit=limit)
