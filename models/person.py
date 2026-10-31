from typing import Self

from pydantic import model_validator

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel, _dedupe


class PersonBase(OpenPechaModel):
    bdrc: NonEmptyStr | None = None
    wiki: NonEmptyStr | None = None
    name: LocalizedString
    alt_names: list[LocalizedString] | None = None

    @model_validator(mode="after")
    def remove_duplicate_alt_names(self) -> Self:
        if self.alt_names is not None:
            self.alt_names = _dedupe(list(self.alt_names), self.name) or None
        return self


class PersonInput(PersonBase):
    pass


class PersonPatch(PatchModel):
    bdrc: NonEmptyStr | None = None
    wiki: NonEmptyStr | None = None
    name: LocalizedString | None = None
    alt_names: list[LocalizedString] | None = None

    @model_validator(mode="after")
    def remove_duplicate_alt_names(self) -> Self:
        if self.alt_names is not None and self.name is not None:
            self.alt_names = _dedupe(list(self.alt_names), self.name)
        return self


class PersonOutput(PersonBase):
    id: NonEmptyStr
