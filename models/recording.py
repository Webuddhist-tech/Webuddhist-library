from itertools import pairwise
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator

from .annotation import Span
from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel
from .contribution import ContributionInputItem, ContributionOutputItem
from .enums import AudioFormat, ContributorRole, LicenseType


def _validate_narrator_roles(contributions: list[ContributionInputItem]) -> list[ContributionInputItem]:
    """Recordings credit the person or AI reading the text, so no other role is accepted."""
    other_roles = sorted({c.role for c in contributions if c.role is not ContributorRole.NARRATOR})
    if other_roles:
        raise ValueError(
            f"Recording contributions must have role '{ContributorRole.NARRATOR}', got: {', '.join(other_roles)}"
        )
    return contributions


NarratorContributions = Annotated[
    list[ContributionInputItem], Field(min_length=1), AfterValidator(_validate_narrator_roles)
]


class RecordingBase(OpenPechaModel):
    title: LocalizedString | None = None
    language: NonEmptyStr | None = None
    license: LicenseType = LicenseType.PUBLIC_DOMAIN_MARK
    date: NonEmptyStr | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class RecordingInput(RecordingBase):
    contributions: NarratorContributions


class RecordingPatch(PatchModel):
    title: LocalizedString | None = None
    language: NonEmptyStr | None = None
    license: LicenseType | None = None
    date: NonEmptyStr | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    contributions: NarratorContributions | None = None


class RecordingOutput(RecordingBase):
    id: NonEmptyStr
    edition_id: NonEmptyStr
    text_id: NonEmptyStr
    contributions: list[ContributionOutputItem]
    format: AudioFormat
    size_bytes: int


class RecordingCue(OpenPechaModel):
    """The stretch of audio during which the text in `span` is being recited."""

    span: Span
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_cue(self) -> Self:
        if self.start_ms >= self.end_ms:
            raise ValueError("'start_ms' must be less than 'end_ms'")
        if self.span.start == self.span.end:
            raise ValueError("a cue span must cover at least one character")
        return self


def _validate_cues_disjoint(cues: list[RecordingCue]) -> list[RecordingCue]:
    for previous, current in pairwise(cues):
        if current.start_ms < previous.end_ms:
            raise ValueError(
                f"cues must be sorted and non-overlapping in time: [{previous.start_ms},{previous.end_ms}) "
                f"is followed by [{current.start_ms},{current.end_ms})"
            )
    return cues


RecordingCues = Annotated[list[RecordingCue], AfterValidator(_validate_cues_disjoint)]
