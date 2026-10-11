"""Ask for a new recipe from the Recipes page, and report back on it.

```
POST   /api/households/recipe-requests              ask for one        (any member)
GET    /api/households/recipe-requests              this household's requests, oldest first
PUT    /api/households/recipe-requests/{item_id}    a filler claiming, finishing or failing one
DELETE /api/households/recipe-requests/{item_id}    cancel a waiting one, or dismiss a finished one
```

**Nothing here finds a recipe.** A request is a note saying "the box could use something new"; the
cluster runner reads it, runs the search and the import, and reports back through `PUT`. Asking
hides nothing, rates nothing and plans nothing, which is the whole difference from the "find me a
new one" die on a recipe card.

Any household member may ask, cancel and report: the recipe box is shared, and so is the question
of what goes in it. The household is fixed by the caller's token through the repository's own
filters; no body or query parameter can name another one.
"""

from functools import cached_property

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import UUID4

from mealie.routes._base.base_controllers import BaseUserController
from mealie.routes._base.controller import controller
from mealie.schema.household.recipe_request import (
    RECIPE_REQUEST_STATUSES,
    RecipeRequestCreate,
    RecipeRequestOut,
    RecipeRequests,
    RecipeRequestUpdate,
)
from mealie.schema.response.responses import ErrorResponse, SuccessResponse
from mealie.services.household_services.recipe_requests import (
    IllegalRecipeRequestTransitionError,
    RecipeRequestNotClearableError,
    RecipeRequestNotFoundError,
    RecipeRequestService,
    TooManyRecipeRequestsError,
    UnknownRecipeError,
)

router = APIRouter(prefix="/households/recipe-requests", tags=["Households: Recipe Requests"])


@controller(router)
class RecipeRequestController(BaseUserController):
    @cached_property
    def service(self) -> RecipeRequestService:
        return RecipeRequestService(self.repos)

    @router.get("", response_model=RecipeRequests)
    def get_all(
        self,
        request_status: list[str] | None = Query(
            None,
            alias="status",
            description=f"Only requests with these statuses: {', '.join(RECIPE_REQUEST_STATUSES)}. "
            "Repeat the parameter for more than one. Defaults to every request the household still has.",
        ),
    ) -> RecipeRequests:
        """This household's recipe requests, oldest first.

        The runner polls `?status=pending`. The Recipes page asks for everything, because a filled
        request stays on screen -- naming the recipe it brought in -- until somebody dismisses it.
        """

        unknown = [s for s in request_status or [] if s not in RECIPE_REQUEST_STATUSES]
        if unknown:
            # an unknown status must say so rather than answer 200 with an empty list, which reads
            # as "nothing is waiting" instead of "that is not a status"
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=ErrorResponse.respond(message=f"status must be one of: {', '.join(RECIPE_REQUEST_STATUSES)}"),
            )

        return RecipeRequests(requests=self.service.list_requests(tuple(request_status) if request_status else None))

    @router.post("", response_model=RecipeRequestOut, status_code=201)
    def create_one(self, data: RecipeRequestCreate) -> RecipeRequestOut:
        """Ask for a new recipe to be found and added to the household's recipes."""

        try:
            return self.service.request(data, self.user.id, self.user.full_name or self.user.username or "")
        except TooManyRecipeRequestsError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=ErrorResponse.respond(message=str(e))) from e

    @router.put("/{item_id}", response_model=RecipeRequestOut)
    def update_one(self, item_id: UUID4, data: RecipeRequestUpdate) -> RecipeRequestOut:
        """A filler reporting: claiming the request, finishing it with the recipe it imported, or
        failing it with a reason. A `filled` report is refused unless the named recipe is really in
        this household's recipes."""

        try:
            return self.service.report(item_id, data)
        except RecipeRequestNotFoundError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ErrorResponse.respond(message=str(e))) from e
        except IllegalRecipeRequestTransitionError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=ErrorResponse.respond(message=str(e))) from e
        except UnknownRecipeError as e:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=ErrorResponse.respond(message=str(e))
            ) from e

    @router.delete("/{item_id}", response_model=SuccessResponse)
    def delete_one(self, item_id: UUID4) -> SuccessResponse:
        """Cancel a request that is still waiting, or dismiss one that has finished.

        A request a search is already running for is refused: that search will import a recipe
        regardless, so "cancelled" would be untrue.
        """

        try:
            self.service.clear(item_id)
        except RecipeRequestNotFoundError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ErrorResponse.respond(message=str(e))) from e
        except RecipeRequestNotClearableError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=ErrorResponse.respond(message=str(e))) from e

        return SuccessResponse(message="recipe request cleared")
