"""Ask for a shopping list to be put in the household's grocery cart, and report back on it.

Four endpoints, and between them they are the whole of the fork's cart surface:

```
POST   /api/households/shopping/lists/{item_id}/cart-request   ask for a fill
DELETE /api/households/shopping/lists/{item_id}/cart-request   cancel a waiting one, or dismiss a finished one
PUT    /api/households/shopping/lists/{item_id}/cart-request   a filler claiming, finishing or failing
GET    /api/households/shopping/cart-requests                  what is outstanding, for the filler to drain
```

**Nothing here buys anything.** A request is a note saying "somebody would like this list in the
cart"; the filling happens elsewhere, and checkout is always a person. The confirmation the
household asked for lives in the UI, deliberately in front of the POST rather than behind it --
by the time a request reaches this module the person has already said yes, and a second server-side
confirmation step would only teach people to click through two dialogs instead of reading one.

Any household member may ask, cancel and report. That is the same boundary the shopping list itself
has -- the list is shared, its contents are shared, and a request to buy what is on it discloses
nothing that the list did not already. The household is fixed by the caller's token through the
repository's own filters; no request body or query parameter can name another one.
"""

from functools import cached_property

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import UUID4

from mealie.routes._base.base_controllers import BaseUserController
from mealie.routes._base.controller import controller
from mealie.schema.household.cart_request import (
    ACTIVE_CART_REQUEST_STATUSES,
    CART_REQUEST_STATUSES,
    CartRequestOut,
    CartRequests,
    CartRequestUpdate,
)
from mealie.schema.response.responses import ErrorResponse, SuccessResponse
from mealie.services.household_services.cart_requests import (
    CartRequestInFlightError,
    CartRequestNotClearableError,
    CartRequestService,
    IllegalCartRequestTransitionError,
    NoCartRequestError,
    NothingToBuyError,
    ShoppingListNotFoundError,
    StaleCartRequestError,
)

router = APIRouter(prefix="/households/shopping", tags=["Households: Cart Requests"])


@controller(router)
class CartRequestController(BaseUserController):
    @cached_property
    def service(self) -> CartRequestService:
        return CartRequestService(self.repos)

    @router.get("/cart-requests", response_model=CartRequests)
    def get_cart_requests(
        self,
        cart_status: str | None = Query(
            None,
            alias="status",
            description=f"Only requests with this status: {', '.join(CART_REQUEST_STATUSES)}. "
            "Defaults to everything still owed a fill.",
        ),
    ) -> CartRequests:
        """Outstanding cart requests across the household, oldest first.

        This is the endpoint the cluster runner polls. It defaults to `pending` and `filling` --
        the requests that still owe somebody a shop -- because a drain loop asking "what is there
        to do" should not have to filter out every fill that has already happened.
        """

        if cart_status is not None and cart_status not in CART_REQUEST_STATUSES:
            # an unknown status must say so rather than answer 200 with an empty list, which reads
            # as "nothing is waiting" instead of "that is not a status"
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=ErrorResponse.respond(message=f"status must be one of: {', '.join(CART_REQUEST_STATUSES)}"),
            )

        statuses = (cart_status,) if cart_status else ACTIVE_CART_REQUEST_STATUSES
        return CartRequests(requests=self.service.active(statuses))

    @router.post("/lists/{item_id}/cart-request", response_model=CartRequestOut, status_code=201)
    def create_cart_request(self, item_id: UUID4) -> CartRequestOut:
        """Ask for this list to be put in the cart.

        Refuses a second request while one is in flight, so a double-press or two people in the
        kitchen at once cannot queue two shops for one list.
        """

        try:
            return self.service.request(item_id, self.user.id, self.user.username or str(self.user.id))
        except ShoppingListNotFoundError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ErrorResponse.respond(message=str(e))) from e
        except CartRequestInFlightError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=ErrorResponse.respond(message=str(e))) from e
        except NothingToBuyError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ErrorResponse.respond(message=str(e))) from e

    @router.delete("/lists/{item_id}/cart-request", response_model=SuccessResponse)
    def delete_cart_request(self, item_id: UUID4) -> SuccessResponse:
        """Cancel a request that is still waiting, or clear one that has finished.

        A request that a filler has already claimed is refused: the cart is being changed at that
        moment, and reporting "cancelled" would be a lie about the state of a real shopping cart.
        """

        try:
            self.service.clear(item_id)
        except (ShoppingListNotFoundError, NoCartRequestError) as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ErrorResponse.respond(message=str(e))) from e
        except CartRequestNotClearableError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=ErrorResponse.respond(message=str(e))) from e

        return SuccessResponse(message="cart request cleared")

    @router.put("/lists/{item_id}/cart-request", response_model=CartRequestOut)
    def update_cart_request(self, item_id: UUID4, data: CartRequestUpdate) -> CartRequestOut:
        """A filler reporting: claiming the request, finishing it, or failing it with a reason.

        The body names the request id it is reporting on, and a mismatch is refused rather than
        applied. A fill that started before somebody cancelled and re-asked would otherwise stamp
        "filled" onto a request nobody has acted on, and the household would believe a shop had
        happened that never did.
        """

        try:
            return self.service.report(item_id, data)
        except (ShoppingListNotFoundError, NoCartRequestError) as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=ErrorResponse.respond(message=str(e))) from e
        except (StaleCartRequestError, IllegalCartRequestTransitionError) as e:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=ErrorResponse.respond(message=str(e))) from e
