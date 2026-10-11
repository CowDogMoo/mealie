"""A request for a new recipe, made from the Recipes page with nothing to anchor it.

The "find me a new one" die on a recipe card asks for something *like that recipe*, and is a
feedback event because it is about one. This is the other ask: "the box could use something new",
optionally with a word about what -- "something with lentils". A person presses the button, a row
is written, and the cluster runner that already drains this household's feedback picks it up,
finds one recipe, imports it, and reports back here. Nothing is hidden, rated or planned.

The status vocabulary and transition table match the cart request's on purpose
(:mod:`mealie.schema.household.cart_request`): same runner, same shape of job, so the same words
mean the same things on both.
"""

from datetime import datetime
from typing import Annotated

from pydantic import UUID4, ConfigDict, Field, StringConstraints, field_validator, model_validator

from mealie.schema._mealie import MealieModel
from mealie.schema._mealie.mealie_model import UpdatedAtField

RECIPE_REQUEST_PENDING = "pending"
"""Asked for, nothing has picked it up yet. The only status a person may cancel."""

RECIPE_REQUEST_FILLING = "filling"
"""Claimed: a recipe search is running for it right now."""

RECIPE_REQUEST_FILLED = "filled"
"""Terminal. A recipe was imported; the request names it."""

RECIPE_REQUEST_FAILED = "failed"
"""Terminal. Nothing was imported; ``result`` says why, and the person can simply ask again."""

RECIPE_REQUEST_STATUSES: tuple[str, ...] = (
    RECIPE_REQUEST_PENDING,
    RECIPE_REQUEST_FILLING,
    RECIPE_REQUEST_FILLED,
    RECIPE_REQUEST_FAILED,
)
"""Every status a recipe request can hold."""

ACTIVE_RECIPE_REQUEST_STATUSES: tuple[str, ...] = (RECIPE_REQUEST_PENDING, RECIPE_REQUEST_FILLING)
"""The statuses that mean "a recipe is still owed"."""

LEGAL_TRANSITIONS: dict[str, tuple[str, ...]] = {
    RECIPE_REQUEST_PENDING: (RECIPE_REQUEST_FILLING, RECIPE_REQUEST_FILLED, RECIPE_REQUEST_FAILED),
    RECIPE_REQUEST_FILLING: (RECIPE_REQUEST_FILLED, RECIPE_REQUEST_FAILED),
    RECIPE_REQUEST_FILLED: (),
    RECIPE_REQUEST_FAILED: (),
}
"""What a status may become. Both terminal statuses are final: another recipe is another request."""

MAX_OPEN_RECIPE_REQUESTS = 5
"""How many requests a household may have waiting at once.

Each one is a recipe search on the household's Claude subscription. Five is more than anybody asks
for in one sitting and few enough that a stuck finger, or a page left reloading, cannot queue an
afternoon of searches."""

NOTE_MAX_LENGTH = 300
"""Long enough for "something with lentils, not too spicy, the kids are eating too", short enough
that a note stays a preference and never becomes a brief of its own."""

_NoteStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTE_MAX_LENGTH)]
_ResultStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class RecipeRequestCreate(MealieModel):
    """What a person sends: at most a note. Who asked is taken from the token, never the body."""

    note: _NoteStr | None = None

    @field_validator("note", mode="before")
    @classmethod
    def blank_note_is_none(cls, value: str | None) -> str | None:
        # the note is read into a recipe-search prompt, so it is kept to one line: a newline is the
        # cheapest way to make a preference look like a new instruction
        if value is None:
            return None
        value = " ".join(str(value).split())
        return value or None


class RecipeRequestSave(RecipeRequestCreate):
    group_id: UUID4
    household_id: UUID4
    status: str = RECIPE_REQUEST_PENDING
    requested_by: UUID4 | None = None
    requested_by_name: str | None = None


class RecipeRequestOut(RecipeRequestSave):
    """A request as it is read back: who asked, what for, where it got to and what answered it."""

    id: UUID4
    result: str | None = None
    """Free text from whatever filled the request -- what it found, or why it could not. Shown to
    the household verbatim, so it is the filler's job to make it a sentence."""

    recipe_id: UUID4 | None = None
    recipe_slug: str | None = None
    recipe_name: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = UpdatedAtField(None)

    model_config = ConfigDict(from_attributes=True)


class RecipeRequestUpdate(MealieModel):
    """A filler reporting progress: claiming a request, finishing it with the recipe it imported,
    or failing it with a reason."""

    status: str
    result: _ResultStr | None = None
    recipe_slug: str | None = None
    """Required with ``filled``. The service reads the recipe back by this slug and refuses the
    report if the household's recipe box does not hold it, so "filled" is never just a claim."""

    @field_validator("status")
    @classmethod
    def validate_status(cls, status: str) -> str:
        # `pending` is reachable only by asking, never by reporting: a filler that wants to give a
        # request back says `failed` with a reason, so why-it-came-back stays on the record
        reportable = (RECIPE_REQUEST_FILLING, RECIPE_REQUEST_FILLED, RECIPE_REQUEST_FAILED)
        if status not in reportable:
            raise ValueError(f"status must be one of: {', '.join(reportable)}")

        return status

    @model_validator(mode="after")
    def filled_names_a_recipe(self):
        if self.status == RECIPE_REQUEST_FILLED and not (self.recipe_slug or "").strip():
            raise ValueError("a filled request must name the recipe it was filled with (recipeSlug)")

        return self


class RecipeRequests(MealieModel):
    """The poll response. A wrapper rather than a bare list, as with cart requests, so the shape can
    grow a cursor later without breaking the runner that reads it."""

    requests: Annotated[list[RecipeRequestOut], Field(default_factory=list)]
