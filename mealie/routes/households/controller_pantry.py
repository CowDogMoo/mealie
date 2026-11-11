"""The household's pantry staples: foods it usually has, which recipes leave off a shopping list.

```
GET    /api/households/pantry                                  staples, and foods worth marking
PUT    /api/households/pantry/{food_id}                        mark a food (and its plain forms)
DELETE /api/households/pantry/{food_id}                        unmark one food
GET    /api/households/pantry/shopping-lists/{list_id}         the staples that list left off
```

A staple is upstream's per-household "on hand" flag and nothing more, so everything that already
reads the flag -- the add-to-list dialog, the foods page, the recipe finder -- agrees with this
list without knowing it exists. Any household member may change it: the pantry is shared, the same
as the shopping list it shortens. The household is fixed by the caller's token; no parameter can
name another one.
"""

from functools import cached_property

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4

from mealie.routes._base.base_controllers import BaseUserController
from mealie.routes._base.controller import controller
from mealie.schema.household.pantry import PantryLeftOff, PantryMarkOut, PantryOut
from mealie.schema.response.responses import ErrorResponse, SuccessResponse
from mealie.services.household_services.pantry import (
    PantryFoodNotFoundError,
    PantryListNotFoundError,
    PantryService,
)

router = APIRouter(prefix="/households/pantry", tags=["Households: Pantry"])


@controller(router)
class PantryController(BaseUserController):
    @cached_property
    def service(self) -> PantryService:
        return PantryService(self.repos)

    @router.get("", response_model=PantryOut)
    def get_pantry(self) -> PantryOut:
        """This household's staples, and unmarked foods that look like ones."""

        return self.service.get()

    @router.get("/shopping-lists/{list_id}", response_model=PantryLeftOff)
    def get_left_off(self, list_id: UUID4) -> PantryLeftOff:
        """The staples the recipes on a shopping list use, which were left off it for that reason."""

        try:
            return self.service.left_off(list_id)
        except PantryListNotFoundError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ErrorResponse.respond(message=str(e))) from e

    @router.put("/{food_id}", response_model=PantryMarkOut)
    def mark_one(self, food_id: UUID4) -> PantryMarkOut:
        """Mark a food as a staple. Plain forms of it ("kosher salt" for salt) are marked with it
        and named in the response; marking a staple again changes nothing."""

        try:
            return self.service.mark(food_id)
        except PantryFoodNotFoundError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ErrorResponse.respond(message=str(e))) from e

    @router.delete("/{food_id}", response_model=SuccessResponse)
    def unmark_one(self, food_id: UUID4) -> SuccessResponse:
        """Take a food off the staples, so recipes put it on the shopping list again."""

        try:
            self.service.unmark(food_id)
        except PantryFoodNotFoundError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ErrorResponse.respond(message=str(e))) from e

        return SuccessResponse(message="food is no longer a pantry staple")
