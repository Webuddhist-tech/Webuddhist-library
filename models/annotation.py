from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import pairwise
from typing import Any, Self

from pydantic import ConfigDict, Field, model_validator

from .base import LocalizedString, NonEmptyStr, OpenPechaModel
from .enums import AttributeType, BibliographyType, SegmentType


class Span(OpenPechaModel):
    """Half-open character range. Empty ranges mark positions, such as headings without their own content."""

    start: int = Field(..., ge=0, description="Start character position (inclusive)")
    end: int = Field(..., ge=0, description="End character position (exclusive)")

    @model_validator(mode="after")
    def validate_span_not_inverted(self) -> Self:
        if self.start > self.end:
            raise ValueError("'start' must not be greater than 'end'")
        return self


def _validate_layout(spans: Sequence[Span], holds: Callable[[Span, Span], bool], requirement: str) -> None:
    """Check how each span sits against the one before it, in the order given."""
    for previous, current in pairwise(spans):
        if not holds(previous, current):
            raise ValueError(
                f"spans must be {requirement}: [{previous.start},{previous.end}) "
                f"is followed by [{current.start},{current.end})"
            )


def _validate_contiguous(spans: Sequence[Span]) -> None:
    _validate_layout(spans, lambda previous, current: current.start == previous.end, "contiguous")


def _validate_disjoint(spans: Sequence[Span]) -> None:
    _validate_layout(spans, lambda previous, current: current.start >= previous.end, "sorted and non-overlapping")


class AnnotationMetadata(OpenPechaModel):
    name: NonEmptyStr | None = None


class SingleSpanAnnotation(OpenPechaModel):
    span: Span
    metadata: AnnotationMetadata | None = None

    @property
    def max_end(self) -> int:
        return self.span.end


class LinesModel(OpenPechaModel):
    lines: list[Span] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_lines_contiguous(self) -> Self:
        _validate_contiguous(self.lines)
        return self

    @property
    def span(self) -> Span:
        return Span(start=self.lines[0].start, end=self.lines[-1].end)


class SegmentInput(LinesModel):
    type: SegmentType = Field(
        default=SegmentType.PARAGRAPH,
        description="Segment type; defaults to 'paragraph'",
    )
    reference: NonEmptyStr | None = None


class SegmentOutput(LinesModel):
    id: NonEmptyStr
    type: SegmentType = SegmentType.PARAGRAPH
    reference: NonEmptyStr | None = None


class SegmentWithContextOutput(SegmentOutput):
    segmentation_id: NonEmptyStr
    edition_id: NonEmptyStr
    text_id: NonEmptyStr
    tag_ids: list[str] | None = Field(default=None)


class RelatedSegmentationOutput(OpenPechaModel):
    segmentation_id: NonEmptyStr
    segments: list[SegmentWithContextOutput]


class SegmentationInput(OpenPechaModel):
    segments: list[SegmentInput] = Field(
        min_length=1, description="Ordered, contiguous segments without overlaps or gaps"
    )
    metadata: AnnotationMetadata | None = None

    @property
    def max_end(self) -> int:
        return self.segments[-1].lines[-1].end

    @model_validator(mode="after")
    def validate_segments_contiguous(self) -> Self:
        _validate_contiguous([segment.span for segment in self.segments])
        return self

    @model_validator(mode="after")
    def validate_segment_references_unique(self) -> Self:
        references = [segment.reference for segment in self.segments if segment.reference is not None]
        if len(references) != len(set(references)):
            raise ValueError("segment references must be unique within a segmentation")
        return self


class SegmentationOutput(OpenPechaModel):
    id: NonEmptyStr
    edition_id: NonEmptyStr
    text_id: NonEmptyStr
    metadata: AnnotationMetadata | None = None


class Page(LinesModel):
    reference: NonEmptyStr

    @model_validator(mode="after")
    def validate_page_nonempty(self) -> Self:
        if self.lines[0].start == self.lines[-1].end:
            raise ValueError("Pages must contain at least one character")
        return self


class Volume(OpenPechaModel):
    model_config = ConfigDict(extra="forbid", json_schema_mode_override="validation")

    index: int | None = Field(default=None, ge=1, description="Volume number in the source collection")
    pages: list[Page] = Field(min_length=1)
    metadata: AnnotationMetadata | None = None

    @property
    def span(self) -> Span:
        return Span(start=self.pages[0].lines[0].start, end=self.pages[-1].lines[-1].end)

    @model_validator(mode="after")
    def validate_pages_contiguous(self) -> Self:
        _validate_contiguous([page.span for page in self.pages])
        return self


class PaginationBase(OpenPechaModel):
    volumes: list[Volume] = Field(min_length=1)
    metadata: AnnotationMetadata | None = None

    @property
    def max_end(self) -> int:
        return max(volume.span.end for volume in self.volumes)


class PaginationInput(PaginationBase):
    @model_validator(mode="after")
    def validate_volume_indexes(self) -> Self:
        if len(self.volumes) == 1:
            return self
        indexes = [volume.index for volume in self.volumes]
        if None in indexes:
            raise ValueError("multiple volumes must have indexes")
        if len(set(indexes)) != len(indexes):
            raise ValueError("volume indexes must be unique")
        return self

    @model_validator(mode="after")
    def validate_volume_spans_disjoint(self) -> Self:
        by_index = sorted(self.volumes, key=lambda volume: volume.index or 0)
        _validate_disjoint([volume.span for volume in by_index])
        return self


class PaginationOutput(PaginationBase):
    id: NonEmptyStr
    edition_id: NonEmptyStr
    text_id: NonEmptyStr


class TableOfContentsSectionInput(OpenPechaModel):
    title: LocalizedString
    summary: LocalizedString | None = None
    span: Span
    subsections: list[TableOfContentsSectionInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_subsections(self) -> Self:
        for subsection in self.subsections:
            if subsection.span.start < self.span.start or subsection.span.end > self.span.end:
                raise ValueError("subsection span must be contained within parent section span")
        _validate_disjoint([subsection.span for subsection in self.subsections])
        return self


class TableOfContentsInput(OpenPechaModel):
    sections: list[TableOfContentsSectionInput] = Field(min_length=1)
    metadata: AnnotationMetadata | None = None

    @property
    def max_end(self) -> int:
        return max(section.span.end for section in self.sections)

    @model_validator(mode="after")
    def validate_sections_disjoint(self) -> Self:
        _validate_disjoint([section.span for section in self.sections])
        return self


class TableOfContentsSectionOutput(OpenPechaModel):
    id: NonEmptyStr
    title: LocalizedString
    summary: LocalizedString | None = None
    span: Span
    subsections: list[TableOfContentsSectionOutput] = Field(default_factory=list)


class TableOfContentsOutput(OpenPechaModel):
    id: NonEmptyStr
    edition_id: NonEmptyStr
    text_id: NonEmptyStr
    sections: list[TableOfContentsSectionOutput]
    metadata: AnnotationMetadata | None = None


class BibliographicMetadataBase(SingleSpanAnnotation):
    type: BibliographyType


class BibliographicMetadataInput(BibliographicMetadataBase):
    pass


class BibliographicMetadataOutput(BibliographicMetadataBase):
    id: NonEmptyStr
    edition_id: NonEmptyStr
    text_id: NonEmptyStr


class NoteBase(SingleSpanAnnotation):
    text: NonEmptyStr


class NoteInput(NoteBase):
    pass


class NoteOutput(NoteBase):
    id: NonEmptyStr
    edition_id: NonEmptyStr
    text_id: NonEmptyStr


class MarkInput(SingleSpanAnnotation):
    pass


class MarkOutput(MarkInput):
    id: NonEmptyStr
    edition_id: NonEmptyStr
    text_id: NonEmptyStr


class AttributeBase(SingleSpanAnnotation):
    type: AttributeType
    value: Any


class AttributeInput(AttributeBase):
    pass


class AttributeOutput(AttributeBase):
    id: NonEmptyStr
