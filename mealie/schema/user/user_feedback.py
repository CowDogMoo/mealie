"""Per-person scoped meal feedback: the vocabulary, its licensing map, and the request schemas.

A thumbs-down records not just "no" but *which* of twelve reasons, and *how wide* that reason
licenses the learning to reach. "I don't like fennel" licenses an ingredient-wide conclusion;
"too much work" licenses nothing beyond the recipe it was cast on.

A person may still claim a scope wider than their reason licenses. That is stored with
``advisory = True``, never rejected -- refusing it would train people to pick a broader reason
than they mean, and the honest signal is worth more than the tidy one. Downstream consumers
apply their own confidence threshold (the planner uses ``CONFIDENCE_THRESHOLD = 2``) before
acting on anything advisory.

This module is the **copy of record** for the vocabulary. The sibling ``plan-weekly-dinners``
skill repository keeps a byte-identical copy in ``history.mjs``; when the two disagree, this
file wins and the skill is corrected.

A note on ``reason`` alongside a non-``down`` vote: PLAN.md section 4 describes ``reason`` as
"required when vote = down, else null", but the explicit 422 list in the same section does not
make a reason on an up-vote an error. We take the permissive reading -- ``down`` *requires* a
reason, other votes merely do not, and a known reason sent with ``up`` or ``neutral`` is stored
as given. Silently discarding a value the client deliberately sent is worse than keeping it.
"""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from mealie.schema._mealie import MealieModel

from .user import UserRatingOut

FEEDBACK_VOTES: tuple[str, ...] = ("up", "down", "neutral")
"""Every vote the API accepts. ``down`` is the only one that requires a reason."""

FEEDBACK_SCOPES: tuple[str, ...] = ("recipe", "dish", "ingredient", "cuisine", "source")
"""Every scope a claim may reach across. ``recipe`` is the default and the narrowest."""

LICENSED_SCOPES: dict[str, tuple[str, ...]] = {
    "i-did-not-like-this-recipe": ("recipe",),
    "i-do-not-like-this-dish": ("recipe", "dish"),
    "i-do-not-like-a-specific-ingredient": ("recipe", "ingredient"),
    "too-much-work": ("recipe",),
    "took-too-long": ("recipe",),
    "too-heavy": ("recipe",),
    "not-flavorful-enough": ("recipe",),
    "too-spicy": ("recipe",),
    "too-repetitive": ("recipe", "dish", "cuisine"),
    "bad-source": ("recipe", "source"),
    "did-not-work-for-our-household": ("recipe",),
    "other": ("recipe",),
}
"""How far each reason licenses a conclusion to be generalized. Mirrors the D2 table in PLAN.md."""

FEEDBACK_REASONS: tuple[str, ...] = tuple(LICENSED_SCOPES)
"""The twelve reasons, in the order the licensing map declares them."""


def is_advisory(reason: str | None, scope: str) -> bool:
    """Whether a claim reaches wider than its reason licenses.

    An advisory claim is stored and returned like any other; it simply carries no license to be
    generalized on its own. Unknown reasons are not treated as advisory: validation rejects them
    on the way in, so a value that reaches here is either a known reason or none at all.
    """

    if reason is None:
        # a scoped claim with nothing to license it
        return scope != "recipe"

    # An unknown reason licenses nothing beyond the recipe it was cast on. Validation rejects
    # unknown reasons on the way in, so this is unreachable through the API today -- but the
    # unsafe answer must not be the default of a public helper, and a reason retired from the
    # vocabulary while old rows still carry it would otherwise read back as licensed.
    licensed = LICENSED_SCOPES.get(reason, ("recipe",))

    return scope not in licensed


_StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True)]


class UserFeedbackIn(MealieModel):
    """The request body of a feedback event. The user and recipe come from the route."""

    vote: str
    reason: Annotated[str | None, Field(validate_default=True)] = None
    scope: Annotated[str, Field(validate_default=True)] = "recipe"
    target: Annotated[_StrippedStr | None, Field(validate_default=True)] = None
    note: _StrippedStr | None = None

    @field_validator("vote")
    @classmethod
    def validate_vote(cls, vote: str) -> str:
        if vote not in FEEDBACK_VOTES:
            raise ValueError(f"vote must be one of: {', '.join(FEEDBACK_VOTES)}")

        return vote

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, reason: str | None, info: ValidationInfo) -> str | None:
        if reason is not None and reason not in LICENSED_SCOPES:
            raise ValueError(f"reason must be one of: {', '.join(FEEDBACK_REASONS)}")

        # `vote` is declared first, so it is already validated and present unless it failed
        if info.data.get("vote") == "down" and not reason:
            raise ValueError("reason is required when vote is 'down'")

        return reason

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, scope: str) -> str:
        if scope not in FEEDBACK_SCOPES:
            raise ValueError(f"scope must be one of: {', '.join(FEEDBACK_SCOPES)}")

        return scope

    @field_validator("target")
    @classmethod
    def validate_target(cls, target: str | None, info: ValidationInfo) -> str | None:
        # whitespace has already been stripped by the annotation, so "" means "nothing was said"
        target = target or None

        scope = info.data.get("scope")
        if scope is not None and scope != "recipe" and not target:
            raise ValueError(f"target is required when scope is '{scope}' rather than 'recipe'")

        return target

    @field_validator("note")
    @classmethod
    def empty_note_is_no_note(cls, note: str | None) -> str | None:
        return note or None


class UserFeedbackCreate(UserFeedbackIn):
    """What the repository writes. ``advisory`` is derived here, never taken from the client."""

    # plain UUID for the foreign keys, UUID4 for our own id further down: the same split
    # CreateGroupPreferences/ReadGroupPreferences makes. Asserting a version on an identifier
    # another table minted rejects valid rows and buys nothing.
    user_id: UUID
    recipe_id: UUID
    advisory: bool = False

    @model_validator(mode="after")
    def compute_advisory(self) -> Self:
        """Overwrite any supplied ``advisory`` with the value the licensing map dictates.

        This runs on ``UserFeedbackOut`` too, since it inherits. That is deliberate: the flag is
        a pure function of ``reason`` and ``scope``, so recomputing a stored row yields the same
        answer, and a row written before a licensing change is re-read under the current map.
        """

        self.advisory = is_advisory(self.reason, self.scope)
        return self


class UserFeedbackOut(UserFeedbackCreate):
    id: UUID4
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class HouseholdUserFeedbackOut(UserFeedbackOut):
    """A household-scoped read: the planner needs to tell the two people apart."""

    username: str


class HouseholdUserRatingOut(UserRatingOut):
    """A household-scoped read of stars, retiring the planner's one-token-per-person dance."""

    username: str


class UserFeedbacks[DataT: BaseModel](BaseModel):
    feedback: list[DataT]
