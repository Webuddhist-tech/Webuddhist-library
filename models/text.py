from typing import Self

from pydantic import Field, model_validator

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel, _dedupe
from .contribution import ContributionInputItem, ContributionOutputItem
from .enums import LicenseType


class TextBase(OpenPechaModel):
    bdrc: NonEmptyStr | None = None
    wiki: NonEmptyStr | None = None
    date: NonEmptyStr | None = None
    title: LocalizedString
    alt_titles: list[LocalizedString] | None = None
    language: NonEmptyStr
    commentary_of: NonEmptyStr | None = None
    translation_of: NonEmptyStr | None = None
    license: LicenseType = LicenseType.PUBLIC_DOMAIN_MARK

    @model_validator(mode="after")
    def validate_text(self) -> Self:
        if self.commentary_of and self.translation_of:
            raise ValueError("Cannot be both a commentary and translation")

        base_language = self.language.split("-")[0].lower()
        title_base_languages = {lang.split("-")[0].lower() for lang in self.title.root}
        if base_language not in title_base_languages:
            raise ValueError(
                f"Title must include an entry for the text's language '{self.language}'. "
                f"Available title languages: {list(self.title.root.keys())}"
            )
        return self

    @model_validator(mode="after")
    def remove_duplicate_alt_titles(self) -> Self:
        if self.alt_titles is not None:
            self.alt_titles = _dedupe(list(self.alt_titles), self.title) or None
        return self


class TextInput(TextBase):
    category_id: NonEmptyStr | None = None
    contributions: list[ContributionInputItem] = Field(default_factory=list)
    tag_ids: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_category(self) -> Self:
        if self.translation_of:
            if "category_id" in self.model_fields_set:
                raise ValueError("Translations inherit their category; category_id must not be supplied")
        elif self.category_id is None:
            raise ValueError("category_id is required for original texts and commentaries")
        return self


class TextPatch(PatchModel):
    bdrc: NonEmptyStr | None = None
    wiki: NonEmptyStr | None = None
    date: NonEmptyStr | None = None
    title: LocalizedString | None = None
    alt_titles: list[LocalizedString] | None = None
    language: NonEmptyStr | None = None
    category_id: NonEmptyStr | None = None
    expected_category_id: NonEmptyStr | None = None
    license: LicenseType | None = None
    contributions: list[ContributionInputItem] | None = None
    tag_ids: list[NonEmptyStr] | None = None

    @model_validator(mode="after")
    def remove_duplicate_alt_titles(self) -> Self:
        if self.expected_category_id is not None and self.category_id is None:
            raise ValueError("expected_category_id requires category_id")
        if self.alt_titles is not None and self.title is not None:
            self.alt_titles = _dedupe(list(self.alt_titles), self.title)
        return self


class TextOutput(TextBase):
    id: NonEmptyStr
    category_id: NonEmptyStr
    contributions: list[ContributionOutputItem]
    commentaries: list[str] = Field(default_factory=list)
    translations: list[str] = Field(default_factory=list)
    editions: list[str] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
