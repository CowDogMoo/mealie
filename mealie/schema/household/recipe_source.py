"""A household's list of recipe sites and how far each one is trusted.

The list is the positive complement of the `bad-source` feedback reason: a `down` vote scoped to
`source` says a site let somebody down once, and this list is where that judgement is kept as
current state, alongside the sites the household has decided are worth trusting outright.
"""

import enum

from pydantic import UUID4, ConfigDict, field_validator

from mealie.schema._mealie import MealieModel
from mealie.schema.response.pagination import PaginationBase
from mealie.services.recipe_sources import normalize_domain


class RecipeSourceStatus(enum.StrEnum):
    known_good = "known-good"
    """Import freely; the household has decided this site's recipes are worth trusting."""

    caution = "caution"
    """Import goes ahead with a warning. The state a site lands in after a `bad-source` vote."""

    blocked = "blocked"
    """Import by URL is refused until a member changes the status."""


class RecipeSourceCreate(MealieModel):
    domain: str
    status: RecipeSourceStatus = RecipeSourceStatus.known_good
    note: str | None = None

    @field_validator("domain")
    @classmethod
    def normalize(cls, value: str) -> str:
        # a full recipe URL is the most natural thing to paste, so it is accepted and reduced to
        # its host; the ValueError from an empty or hostless value becomes a 422
        return normalize_domain(value)

    @field_validator("note")
    @classmethod
    def blank_note_is_none(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class RecipeSourceSave(RecipeSourceCreate):
    group_id: UUID4
    household_id: UUID4


class RecipeSourceOut(RecipeSourceSave):
    id: UUID4
    model_config = ConfigDict(from_attributes=True)


class RecipeSourcePagination(PaginationBase):
    items: list[RecipeSourceOut]


class RecipeSourceLookupOut(MealieModel):
    """The answer to "what does this household think of the site behind this URL?"

    `domain` is the normalized host that was asked about; `source` is the list entry that
    answered, which may be a parent domain of it, or `None` when nothing on the list covers it.
    """

    domain: str
    status: str | None = None
    """`source.status` lifted to the top level for callers that only want the verdict. A plain string
    rather than `RecipeSourceStatus | None` because the TypeScript generator emits a dangling
    `RecipeSourceStatus1` alias for the nullable enum; the value is still always one of the enum's
    members, or `None` when nothing on the list covers the site."""

    source: RecipeSourceOut | None = None
