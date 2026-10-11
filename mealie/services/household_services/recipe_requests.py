"""The rules behind "Find a new recipe": a cap on how many may wait, legal transitions, and a filled
request that has to name a recipe the household can actually see.

Asking is cheap for a person and not for the household: each request is a recipe search on the
household's Claude subscription. So the cap lives here rather than in the UI, where a reloaded page
or a second phone would walk straight past it.

One race is left open on purpose, as it is for cart requests: two presses in the same instant can
both read "four waiting" and both write a fifth. The consequence is one search more than the cap,
which is not worth a lock for a two-person household.
"""

from pydantic import UUID4

from mealie.repos.repository_factory import AllRepositories
from mealie.schema.household.recipe_request import (
    ACTIVE_RECIPE_REQUEST_STATUSES,
    LEGAL_TRANSITIONS,
    MAX_OPEN_RECIPE_REQUESTS,
    RECIPE_REQUEST_FILLED,
    RECIPE_REQUEST_FILLING,
    RECIPE_REQUEST_PENDING,
    RecipeRequestCreate,
    RecipeRequestOut,
    RecipeRequestSave,
    RecipeRequestUpdate,
)
from mealie.schema.response.pagination import OrderDirection, PaginationQuery
from mealie.services._base_service import BaseService


class RecipeRequestError(Exception):
    """Base for the refusals this service raises. The controller maps each to a status code."""


class RecipeRequestNotFoundError(RecipeRequestError):
    """No request with that id belongs to this household."""


class TooManyRecipeRequestsError(RecipeRequestError):
    """The household already has as many requests waiting as it may."""


class RecipeRequestNotClearableError(RecipeRequestError):
    """Cancelling was asked for while a search was already running for the request."""


class IllegalRecipeRequestTransitionError(RecipeRequestError):
    """A report asks for a status the current one cannot become."""


class UnknownRecipeError(RecipeRequestError):
    """A `filled` report names a recipe this household's recipe box does not hold."""


class RecipeRequestService(BaseService):
    """Reads and writes recipe requests over a household-scoped repository set.

    `repos` must be household-scoped. The repository's own group and household filters are the whole
    access boundary -- nothing here compares ids itself -- so a group-wide `AllRepositories` would let
    one household cancel or answer another's request.
    """

    def __init__(self, repos: AllRepositories) -> None:
        super().__init__()
        self.repos = repos
        self.requests = repos.recipe_requests

    # ------------------------------------------------------------------ reads

    def list_requests(self, statuses: tuple[str, ...] | None = None) -> list[RecipeRequestOut]:
        """This household's requests, oldest first, optionally only those in `statuses`.

        Oldest first because that is the order a drain should work in, and the order a person asked.
        """

        query_filter = None
        if statuses:
            quoted = ", ".join(f'"{s}"' for s in statuses)
            query_filter = f"status IN [{quoted}]"

        page = self.requests.page_all(
            PaginationQuery(
                page=1,
                per_page=-1,
                order_by="created_at",
                order_direction=OrderDirection.asc,
                query_filter=query_filter,
            )
        )
        return page.items

    def get(self, request_id: UUID4) -> RecipeRequestOut:
        found = self.requests.get_one(request_id)
        if found is None:
            raise RecipeRequestNotFoundError(f"no recipe request {request_id} in this household")

        return found

    # ------------------------------------------------------------------ writes

    def request(self, data: RecipeRequestCreate, user_id: UUID4, user_name: str) -> RecipeRequestOut:
        """Ask for a new recipe. Refused once the household has `MAX_OPEN_RECIPE_REQUESTS` waiting."""

        waiting = self.list_requests(ACTIVE_RECIPE_REQUEST_STATUSES)
        if len(waiting) >= MAX_OPEN_RECIPE_REQUESTS:
            raise TooManyRecipeRequestsError(
                f"this household already has {len(waiting)} recipe requests waiting; "
                "wait for one to be filled, or cancel one, before asking again"
            )

        return self.requests.create(
            RecipeRequestSave(
                note=data.note,
                group_id=self.repos.group_id,  # type: ignore[arg-type]
                household_id=self.repos.household_id,  # type: ignore[arg-type]
                status=RECIPE_REQUEST_PENDING,
                requested_by=user_id,
                requested_by_name=user_name,
            )
        )

    def report(self, request_id: UUID4, data: RecipeRequestUpdate) -> RecipeRequestOut:
        """A filler claiming a request, finishing it with the recipe it imported, or failing it."""

        current = self.get(request_id)
        if data.status not in LEGAL_TRANSITIONS.get(current.status, ()):
            raise IllegalRecipeRequestTransitionError(f"a {current.status} recipe request cannot become {data.status}")

        changes: dict = {"status": data.status, "result": data.result}
        if data.status == RECIPE_REQUEST_FILLED:
            # read back, not believed: a report that the box holds a recipe it does not hold would
            # tell the household something arrived when nothing did
            slug = (data.recipe_slug or "").strip()
            recipe = self.repos.recipes.get_one(slug, key="slug")
            if recipe is None:
                raise UnknownRecipeError(f"no recipe with slug {slug!r} in this household's recipes")

            changes |= {"recipe_id": recipe.id, "recipe_slug": recipe.slug, "recipe_name": recipe.name}

        return self.requests.patch(request_id, changes)

    def clear(self, request_id: UUID4) -> None:
        """Cancel a request that is still waiting, or dismiss one that has finished.

        A request a search is already running for is refused: the search will import a recipe
        whatever happens here, and saying "cancelled" would be untrue about the recipe box.
        """

        current = self.get(request_id)
        if current.status == RECIPE_REQUEST_FILLING:
            raise RecipeRequestNotClearableError(
                "a recipe search is already running for this request; it can be dismissed once it finishes"
            )

        self.requests.delete(request_id)
