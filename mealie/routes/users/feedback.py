"""The self-only half of scoped meal feedback: cast an event, read your own back, undo one.

Every handler here calls ``assert_user_change_allowed`` before it touches a repository, exactly
as ``ratings.py`` does. PLAN.md D4 widens *reads* to the household on a separate router; nothing
on this one ever answers for somebody else, so a cross-user call is a 403 before any data is
loaded. That asymmetry -- household reads, self-only writes -- is the fork's whole security
argument, which is why the guard is on the read here too rather than only on the write.
"""

from functools import cached_property
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import UUID4

from mealie.repos.all_repositories import get_repositories
from mealie.routes._base import BaseUserController, controller
from mealie.routes._base.routers import UserAPIRouter
from mealie.routes.users._helpers import assert_user_change_allowed
from mealie.schema.response.responses import ErrorResponse
from mealie.schema.user.user import UserRatingCreate
from mealie.schema.user.user_feedback import UserFeedbackCreate, UserFeedbackIn, UserFeedbackOut, UserFeedbacks

router = UserAPIRouter()

VOTE_STAR_RATINGS: dict[str, float] = {"down": 1, "up": 5}
"""PLAN.md D3: a vote also writes the caster's star, since nobody who just pressed thumbs-down
in the planner is then going to go set stars. ``neutral`` is deliberately absent from this map --
it records an opinion too mild to be worth overwriting a star the person chose deliberately."""


@controller(router)
class UserFeedbackController(BaseUserController):
    @cached_property
    def group_recipes(self):
        # household_id=None on purpose: households inside a group share their recipes, and a
        # person can perfectly well have an opinion about a dish another household cooked.
        return get_repositories(self.session, group_id=self.group_id, household_id=None).recipes

    def get_recipe_or_404(self, slug_or_id: str | UUID):
        """Fetches a recipe by slug or id, or raises a 404 error if not found.

        Deliberately identical to ``UserRatingsController.get_recipe_or_404``: these two
        controllers address recipes the same way, and ``ratings.py`` belongs to upstream, so
        hoisting the shared copy into a helper would widen this fork's rebase conflict surface
        (PLAN.md section 7) to buy nine lines.
        """

        if isinstance(slug_or_id, str):
            try:
                slug_or_id = UUID(slug_or_id)
            except ValueError:
                pass

        if isinstance(slug_or_id, UUID):
            recipe = self.group_recipes.get_one(slug_or_id, key="id")
        else:
            recipe = self.group_recipes.get_one(slug_or_id, key="slug")

        if not recipe:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse.respond(message="Not found."),
            )

        return recipe

    def sync_star_rating(self, user_id: UUID4, recipe_id: UUID4, vote: str) -> None:
        """Project a vote onto the caster's own star rating, per D3.

        Create-or-update through ``user_ratings``, the same path ``UserRatingsController`` takes,
        so the pair stays under its one-row-per-(user, recipe) unique constraint and upstream's
        aggregate-rating listeners fire exactly as they would for a star set by hand. Only the
        caster's row is touched, and never ``recipes.rating`` directly.

        Overwriting a star this person set earlier is correct rather than lossy: it is the same
        person expressing the same judgement in the same second. The event log keeps the history
        the star cannot.
        """

        rating = VOTE_STAR_RATINGS.get(vote)
        if rating is None:
            return

        user_rating = self.repos.user_ratings.get_by_user_and_recipe(user_id, recipe_id)
        if not user_rating:
            self.repos.user_ratings.create(
                UserRatingCreate(user_id=user_id, recipe_id=recipe_id, rating=rating, is_favorite=False)
            )
        else:
            # is_favorite is left exactly as it was; a vote says nothing about favoriting
            user_rating.rating = rating
            self.repos.user_ratings.update(user_rating.id, user_rating)

    @router.post("/{id}/feedback/{slug}", response_model=UserFeedbackOut, status_code=status.HTTP_201_CREATED)
    def create_feedback(self, id: UUID4, slug: str, data: UserFeedbackIn) -> UserFeedbackOut:
        """Record one feedback event on a recipe, plus the star rating that vote implies."""

        assert_user_change_allowed(id, self.user, self.user)

        recipe = self.get_recipe_or_404(slug)

        # UserFeedbackCreate recomputes `advisory` from the licensing map in its model validator,
        # and the request body has no `advisory`, `user_id` or `recipe_id` field to smuggle one
        # in through: the identity comes from the route, the flag from the vocabulary.
        event = self.repos.user_feedback.create(
            UserFeedbackCreate(**data.model_dump(), user_id=id, recipe_id=recipe.id)
        )

        # after the event, which is the record; the star is a projection of it (D3)
        self.sync_star_rating(id, recipe.id, event.vote)

        return event

    @router.get("/{id}/feedback", response_model=UserFeedbacks[UserFeedbackOut])
    def get_feedback(self, id: UUID4) -> UserFeedbacks[UserFeedbackOut]:
        """Get every feedback event this user has cast, oldest first."""

        assert_user_change_allowed(id, self.user, self.user)
        return UserFeedbacks(feedback=self.repos.user_feedback.get_by_user(id))

    @router.delete("/{id}/feedback/{event_id}", response_model=UserFeedbackOut)
    def delete_feedback(self, id: UUID4, event_id: UUID4) -> UserFeedbackOut:
        """Undo one of this user's own feedback events.

        An id that matches nothing and an id that matches somebody else's event answer alike
        with 404. A 403 on the second would confirm that the guessed id exists, which is more
        than a caller who guessed it is entitled to learn. The repository's group filter already
        hides other groups; this check hides other people inside the group.

        The star this event wrote is left where it is: a rating is one mutable value with no
        history, so there is nothing to roll it back to.
        """

        assert_user_change_allowed(id, self.user, self.user)

        event = self.repos.user_feedback.get_one(event_id)
        if event is None or event.user_id != id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse.respond(message="Not found."),
            )

        return self.repos.user_feedback.delete(event_id)
