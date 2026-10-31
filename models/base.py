from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictStr,
    StringConstraints,
    model_validator,
)

type LanguageFilter = Annotated[str, StringConstraints(to_lower=True, pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")]

type NonEmptyStr = Annotated[StrictStr, StringConstraints(min_length=1, pattern=r"\S")]


def _dedupe[T](items: list[T], exclude: T) -> list[T]:
    """Remove duplicates and excluded item from list."""
    seen: list[T] = []
    for item in items:
        if item != exclude and item not in seen:
            seen.append(item)
    return seen


class OpenPechaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchModel(OpenPechaModel):
    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            null_fields = [str(field) for field, value in data.items() if value is None]
            if null_fields:
                raise ValueError(f"Null values are not supported in PATCH: {', '.join(null_fields)}")
        return data

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided for update")
        return self


class LocalizedString(RootModel[dict[str, NonEmptyStr]]):
    root: dict[str, NonEmptyStr] = Field(min_length=1)

    def __getitem__(self, item: str) -> str:
        return self.root[item]
