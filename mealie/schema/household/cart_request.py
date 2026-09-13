"""A request to fill the household's grocery cart from a shopping list.

One person presses "Send to Whole Foods" on a list; something else -- today the cluster runner
that already drains this household's feedback events -- picks the request up, fills the cart, and
says what happened. Nothing here places an order. The confirmation happens in the UI *before* a
request is written, and checkout stays a human step forever.

**Why the state lives in the shopping list's `extras` rather than a table of its own.** A cart
request is current state with exactly one outcome, not a history: the useful question is "is a
fill in flight, and how did the last one end", never "how many times has this been asked". That
is the opposite of `user_recipe_feedback`, which is an append-only log precisely because
confidence-by-repetition needs every repeat to survive. `ShoppingListExtras` already exists,
``ShoppingListSummary`` already eager-loads it, and a poller can therefore read every list's
request state from the list endpoint it was already calling -- no migration, no new join.

The cost of that choice is real and worth stating: extras are string key/value pairs with no
constraints, so every invariant here (one live request at a time, legal transitions, the
requester's identity) is enforced in :mod:`mealie.services.household_services.cart_requests` and
nowhere else. Writing the extras directly through ``PUT /households/shopping/lists/{id}`` would
bypass all of it, which is exactly why the endpoints exist.
"""

from datetime import datetime
from typing import Annotated

from pydantic import UUID4, Field, StringConstraints, field_validator

from mealie.schema._mealie import MealieModel

CART_REQUEST_PENDING = "pending"
"""Asked for, nothing has picked it up yet. The only status a person may cancel."""

CART_REQUEST_FILLING = "filling"
"""Claimed: something is putting items in the cart right now. Cancelling can no longer un-buy."""

CART_REQUEST_FILLED = "filled"
"""Terminal. The cart holds what the list asked for, and a person still has to check out."""

CART_REQUEST_FAILED = "failed"
"""Terminal. The fill stopped early; ``result`` says why, and the list can be re-sent."""

CART_REQUEST_STATUSES: tuple[str, ...] = (
    CART_REQUEST_PENDING,
    CART_REQUEST_FILLING,
    CART_REQUEST_FILLED,
    CART_REQUEST_FAILED,
)
"""Every status a cart request can hold."""

ACTIVE_CART_REQUEST_STATUSES: tuple[str, ...] = (CART_REQUEST_PENDING, CART_REQUEST_FILLING)
"""The statuses that mean "a fill is still owed". A list may hold at most one request in these."""

LEGAL_TRANSITIONS: dict[str, tuple[str, ...]] = {
    CART_REQUEST_PENDING: (CART_REQUEST_FILLING, CART_REQUEST_FILLED, CART_REQUEST_FAILED),
    CART_REQUEST_FILLING: (CART_REQUEST_FILLED, CART_REQUEST_FAILED),
    CART_REQUEST_FILLED: (),
    CART_REQUEST_FAILED: (),
}
"""What a status may become. ``pending`` may go straight to a terminal status because a filler
that finishes inside one tick should not have to claim first, and both terminal statuses are
final -- a new shop is a new request, not a revived one."""

# Extras keys. Namespaced with a `cart` prefix because extras is a shared, unconstrained bag that
# any integration may write into; these are camelCase to match how every other Mealie extras
# consumer (and the JSON these models serialize to) spells its keys.
EXTRA_REQUEST_ID = "cartRequestId"
EXTRA_STATUS = "cartRequestStatus"
EXTRA_REQUESTED_AT = "cartRequestedAt"
EXTRA_REQUESTED_BY = "cartRequestedBy"
EXTRA_REQUESTED_BY_NAME = "cartRequestedByName"
EXTRA_ITEM_COUNT = "cartRequestItemCount"
EXTRA_UPDATED_AT = "cartRequestUpdatedAt"
EXTRA_RESULT = "cartRequestResult"

CART_REQUEST_EXTRAS_KEYS: tuple[str, ...] = (
    EXTRA_REQUEST_ID,
    EXTRA_STATUS,
    EXTRA_REQUESTED_AT,
    EXTRA_REQUESTED_BY,
    EXTRA_REQUESTED_BY_NAME,
    EXTRA_ITEM_COUNT,
    EXTRA_UPDATED_AT,
    EXTRA_RESULT,
)
"""Every key this feature owns. Clearing a request removes exactly these and leaves the rest of
the list's extras -- which belong to other integrations -- untouched."""

_ResultStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class CartRequestOut(MealieModel):
    """A cart request as it is read back: who asked, for which list, and where it got to."""

    shopping_list_id: UUID4
    shopping_list_name: str | None = None
    request_id: UUID4
    status: str
    requested_at: datetime
    requested_by: UUID4
    requested_by_name: str
    item_count: int
    """How many items were still un-ticked when the request was made.

    A snapshot, deliberately not recomputed on read: it is what the person was shown in the
    confirmation dialog and agreed to, so it stays comparable with what the filler reports back
    even though the list keeps moving underneath it.
    """

    updated_at: datetime | None = None
    result: str | None = None
    """Free text from whatever filled the cart -- what went in, what it could not find, why it
    stopped. Shown to the household verbatim, so it is the filler's job to make it a sentence."""


class CartRequestUpdate(MealieModel):
    """A filler reporting progress. ``request_id`` is what stops a late report landing on a
    request that has since been cancelled and re-made."""

    request_id: UUID4
    status: str
    result: _ResultStr | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, status: str) -> str:
        # `pending` is reachable only by asking, never by reporting: a filler that wants to give a
        # request back should say `failed` with a reason, which keeps why-it-came-back in the log
        # instead of silently re-queueing it.
        reportable = (CART_REQUEST_FILLING, CART_REQUEST_FILLED, CART_REQUEST_FAILED)
        if status not in reportable:
            raise ValueError(f"status must be one of: {', '.join(reportable)}")

        return status


class CartRequests(MealieModel):
    """The poll response. A wrapper rather than a bare list so the shape can gain a cursor or a
    server timestamp later without breaking the runner that reads it."""

    requests: Annotated[list[CartRequestOut], Field(default_factory=list)]
