"""Household-scoped reads of every member's feedback events and star ratings.

Upstream restricts rating reads to the caller (`assert_user_change_allowed` in
`mealie/routes/users/ratings.py`), which is why the household's meal planner authenticates twice,
once per person's token, just to see both vote sets. Inside a household, meal plans, shopping
lists and cookbooks are already shared -- the household *is* the trust boundary -- so reading a
housemate's vote discloses nothing they had not already shared. That is PLAN.md D4, and these two
endpoints are the whole of it: one service token, one call, both vote sets.

**Reads widen to the household. Writes do not widen at all.** Nothing in this module writes; every
write still lives on `/api/users/{id}/...` behind `assert_user_change_allowed`.

The disclosure boundary is the member list, and it is derived entirely from `self.household_id`,
which comes from the caller's token. There is deliberately no query parameter, header or body
field on either endpoint by which a caller could name a different household, group or user: the
three filters `/feedback` accepts only ever narrow the set the caller is already entitled to.
"""

from datetime import datetime
from functools import cached_property
from uuid import UUID

from fastapi import HTTPException, Query, status
from pydantic import UUID4

from mealie.routes._base.base_controllers import BaseUserController
from mealie.routes._base.controller import controller
from mealie.routes._base.routers import UserAPIRouter
from mealie.schema.response.responses import ErrorResponse
from mealie.schema.user.user import UserOut, UserRatings
from mealie.schema.user.user_feedback import (
    FEEDBACK_VOTES,
    HouseholdUserFeedbackOut,
    HouseholdUserRatingOut,
    UserFeedbacks,
)

router = UserAPIRouter(prefix="/households", tags=["Households: Feedback"])


@controller(router)
class HouseholdFeedbackController(BaseUserController):
    @cached_property
    def members(self) -> list[UserOut]:
        """Every user in the caller's household, and nobody else.

        `household_id` comes from the authenticated user, never from the request, and `multi_query`
        binds it as a parameter rather than composing a filter string. The repository adds the
        caller's `group_id` on top of it, so both ids are checked together.

        No limit is passed, so the answer is the whole household however large it grows. Reaching
        for `page_all` instead would silently cap it at `PaginationQuery.per_page`, which is 50 --
        right for a household of two and wrong the day one has fifty-one members.
        """

        return self.repos.users.multi_query({"household_id": self.household_id}, override_schema=UserOut)

    @cached_property
    def usernames(self) -> dict[UUID, str]:
        """Member id -> display name, resolved once per request and reused for every row.

        `username` is not a column on either events or ratings, and neither repository joins the
        user in, so the name has to come from the member list that was fetched anyway to decide the
        boundary. One map, not a lookup per row.

        The column is nullable while `HouseholdUserFeedbackOut.username` is not, so a member with no
        username falls back to their id. That keeps one nameless member from failing the whole read,
        and still tells the two people apart, which is all the caller wants a name for.
        """

        return {member.id: member.username or str(member.id) for member in self.members}

    @router.get("/feedback", response_model=UserFeedbacks[HouseholdUserFeedbackOut])
    def get_household_feedback(
        self,
        recipe_id: UUID4 | None = Query(None, alias="recipeId", description="Only events cast on this recipe"),
        since: datetime | None = Query(
            None, description="Only events created at or after this instant (ISO-8601, inclusive)"
        ),
        vote: str | None = Query(None, description=f"Only events with this vote: {', '.join(FEEDBACK_VOTES)}"),
    ) -> UserFeedbacks[HouseholdUserFeedbackOut]:
        """Every feedback event cast by every member of the caller's household, oldest first.

        All three filters are optional and are handed to the repository, which turns each into a
        SQL predicate. None of them can reach outside the household: the user set is fixed before
        they are applied.
        """

        # an unknown vote must say so rather than answer 200 with an empty list, which would read
        # as "nobody voted that way" instead of "that is not a vote"
        if vote is not None and vote not in FEEDBACK_VOTES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=ErrorResponse.respond(message=f"vote must be one of: {', '.join(FEEDBACK_VOTES)}"),
            )

        events = self.repos.user_feedback.get_by_users(
            [member.id for member in self.members],
            recipe_id=recipe_id,
            since=since,
            vote=vote,
        )

        # the repository's oldest-first order is the contract -- consumers count how often a reason
        # has been repeated over time -- so the rows are decorated in place, never re-sorted
        return UserFeedbacks[HouseholdUserFeedbackOut](
            feedback=[event.cast(HouseholdUserFeedbackOut, username=self.usernames[event.user_id]) for event in events]
        )

    @router.get("/ratings", response_model=UserRatings[HouseholdUserRatingOut])
    def get_household_ratings(self) -> UserRatings[HouseholdUserRatingOut]:
        """Every star rating held by every member of the caller's household.

        Grouped by member, members in username order. A rating is current state rather than an
        event -- `UserRatingOut` carries no timestamp -- so there is no chronology to preserve the
        way there is for feedback. Grouping reproduces what the planner used to assemble from one
        authenticated call per person, and ordering the groups by name keeps the response stable
        across calls instead of following whatever order the database happens to return members in.
        Within a member the repository's own order is left alone.
        """

        # one group-filtered query for the whole household, not one per member. get_by_users
        # applies the caller's group filter; get_by_user does not, and a user moved between
        # groups keeps rows pointing at their old group's recipes, which would then surface here.
        by_member: dict[UUID, list] = {}
        for rating in self.repos.user_ratings.get_by_users([member.id for member in self.members]):
            by_member.setdefault(rating.user_id, []).append(rating)

        rows: list[HouseholdUserRatingOut] = []
        for member in sorted(self.members, key=lambda m: (self.usernames[m.id].casefold(), str(m.id))):
            username = self.usernames[member.id]
            rows.extend(
                rating.cast(HouseholdUserRatingOut, username=username) for rating in by_member.get(member.id, [])
            )

        return UserRatings[HouseholdUserRatingOut](ratings=rows)
