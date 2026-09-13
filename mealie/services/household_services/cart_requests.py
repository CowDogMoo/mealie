"""The rules behind "Send to Whole Foods": one live request per list, legal transitions, and the
snapshot of what was asked for.

Every invariant this feature has lives here. The state is a handful of string key/value pairs in
`shopping_list_extras` (see :mod:`mealie.schema.household.cart_request` for why), and extras carry
no constraints of their own, so a caller that writes them directly through
`PUT /households/shopping/lists/{id}` gets no checking at all. That is the trade the storage choice
makes, and it is the reason the cart-request endpoints exist rather than a documented convention.

One race is deliberately left open: two people pressing the button in the same instant can both
read "no request live" and both write one, and the second write wins. Closing it properly needs a
uniqueness constraint that extras cannot express. The consequence is bounded -- the loser's request
id is simply gone, one fill happens, nothing is bought twice -- which is the right price for not
adding a table to a two-person household.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import UUID4

from mealie.repos.repository_factory import AllRepositories
from mealie.schema.household.cart_request import (
    ACTIVE_CART_REQUEST_STATUSES,
    CART_REQUEST_EXTRAS_KEYS,
    CART_REQUEST_FILLING,
    CART_REQUEST_PENDING,
    CART_REQUEST_STATUSES,
    EXTRA_ITEM_COUNT,
    EXTRA_REQUEST_ID,
    EXTRA_REQUESTED_AT,
    EXTRA_REQUESTED_BY,
    EXTRA_REQUESTED_BY_NAME,
    EXTRA_RESULT,
    EXTRA_STATUS,
    EXTRA_UPDATED_AT,
    LEGAL_TRANSITIONS,
    CartRequestOut,
    CartRequestUpdate,
)
from mealie.schema.household.group_shopping_list import ShoppingListOut, ShoppingListSummary
from mealie.services._base_service import BaseService


class CartRequestError(Exception):
    """Base for the refusals this service raises. The controller maps each to a status code."""


class ShoppingListNotFoundError(CartRequestError):
    """No list with that id belongs to this household. Raised before any rule about requests is
    applied, so a refusal never tells a stranger that a list they cannot see exists."""


class CartRequestInFlightError(CartRequestError):
    """A request is already pending or filling on this list."""


class NothingToBuyError(CartRequestError):
    """Every item on the list is ticked off, so there is nothing to put in a cart."""


class NoCartRequestError(CartRequestError):
    """The list has no cart request to cancel or report on."""


class CartRequestNotClearableError(CartRequestError):
    """Clearing was asked for while a filler was mid-fill, when stopping is no longer possible."""


class StaleCartRequestError(CartRequestError):
    """A report names a request id the list is no longer holding."""


class IllegalCartRequestTransitionError(CartRequestError):
    """A report asks for a status the current one cannot become."""


class CartRequestService(BaseService):
    """Reads and writes cart requests over a household-scoped repository set.

    `repos` must be household-scoped. The repository's own group and household filters are the
    entire access boundary here -- nothing in this service compares ids itself -- so handing it a
    group-wide `AllRepositories` would let one household cancel another's shop.
    """

    def __init__(self, repos: AllRepositories) -> None:
        super().__init__()
        self.repos = repos
        self.shopping_lists = repos.group_shopping_lists

    # ------------------------------------------------------------------ reads

    def parse(self, shopping_list: ShoppingListOut | ShoppingListSummary) -> CartRequestOut | None:
        """The cart request held in `shopping_list`'s extras, or None if it holds none.

        A partial or unparseable set of keys reads as *no request* rather than raising. These rows
        are hand-editable strings; one list with a mangled timestamp must not be able to fail the
        household-wide poll that the runner depends on, so the damage is contained to the list it
        is on and logged.
        """

        extras = shopping_list.extras or {}
        status = extras.get(EXTRA_STATUS)
        if not status:
            return None

        if status not in CART_REQUEST_STATUSES:
            self.logger.warning(
                "shopping list %s holds an unknown cart request status %r; ignoring it",
                shopping_list.id,
                status,
            )
            return None

        try:
            return CartRequestOut(
                shopping_list_id=shopping_list.id,
                shopping_list_name=shopping_list.name,
                request_id=UUID(str(extras[EXTRA_REQUEST_ID])),
                status=status,
                requested_at=datetime.fromisoformat(str(extras[EXTRA_REQUESTED_AT])),
                requested_by=UUID(str(extras[EXTRA_REQUESTED_BY])),
                requested_by_name=str(extras.get(EXTRA_REQUESTED_BY_NAME) or extras[EXTRA_REQUESTED_BY]),
                item_count=int(str(extras.get(EXTRA_ITEM_COUNT) or 0)),
                updated_at=(
                    datetime.fromisoformat(str(extras[EXTRA_UPDATED_AT])) if extras.get(EXTRA_UPDATED_AT) else None
                ),
                result=(str(extras[EXTRA_RESULT]) if extras.get(EXTRA_RESULT) else None),
            )
        except (KeyError, TypeError, ValueError):
            self.logger.warning(
                "shopping list %s holds a cart request that cannot be read; ignoring it",
                shopping_list.id,
                exc_info=True,
            )
            return None

    def get(self, shopping_list_id: UUID4) -> CartRequestOut | None:
        """The cart request on one list. Raises `ShoppingListNotFoundError` if it is not ours."""

        return self.parse(self._require_list(shopping_list_id))

    def active(self, statuses: tuple[str, ...] = ACTIVE_CART_REQUEST_STATUSES) -> list[CartRequestOut]:
        """Every request in the household whose status is in `statuses`, oldest request first.

        Oldest first because this is what a filler drains: the person who asked first should be
        served first, and a stable order keeps two ticks from fighting over which to take.

        Reads the summary schema, which loads extras but not list items -- the item count comes
        from the request snapshot, so there is no reason to pull every line of every list.
        """

        requests = []
        for shopping_list in self.shopping_lists.get_all(override=ShoppingListSummary):
            request = self.parse(shopping_list)
            if request is not None and request.status in statuses:
                requests.append(request)

        return sorted(requests, key=lambda r: (r.requested_at, str(r.shopping_list_id)))

    # ----------------------------------------------------------------- writes

    def request(self, shopping_list_id: UUID4, user_id: UUID4, username: str) -> CartRequestOut:
        """Record a new request on `shopping_list_id`.

        Refuses when a request is already in flight, and when nothing on the list is un-ticked --
        an empty shop is a mistake worth reporting, not a no-op worth queueing, because the person
        pressing the button believes something is about to be bought.
        """

        shopping_list = self._require_list(shopping_list_id)

        existing = self.parse(shopping_list)
        if existing is not None and existing.status in ACTIVE_CART_REQUEST_STATUSES:
            raise CartRequestInFlightError(
                f"{shopping_list.name or 'this list'} already has a cart request that is {existing.status}"
            )

        item_count = sum(1 for item in shopping_list.list_items if not item.checked)
        if not item_count:
            raise NothingToBuyError("every item on this list is already ticked off")

        now = datetime.now(UTC)
        return self._write_request(
            shopping_list,
            {
                EXTRA_REQUEST_ID: str(uuid4()),
                EXTRA_STATUS: CART_REQUEST_PENDING,
                EXTRA_REQUESTED_AT: now.isoformat(),
                EXTRA_REQUESTED_BY: str(user_id),
                EXTRA_REQUESTED_BY_NAME: username,
                EXTRA_ITEM_COUNT: str(item_count),
                EXTRA_UPDATED_AT: now.isoformat(),
                EXTRA_RESULT: None,
            },
        )

    def clear(self, shopping_list_id: UUID4) -> None:
        """Take the cart request off a list: cancel it if it is waiting, dismiss it if it finished.

        `filling` is the one status that refuses. A filler is putting things in a real cart at that
        moment, and answering 200 to "cancel" would tell the household the shop had been called off
        when it had not -- so the refusal says to empty the cart in the store instead.
        """

        shopping_list = self._require_list(shopping_list_id)

        existing = self.parse(shopping_list)
        if existing is None:
            raise NoCartRequestError("this list has no cart request")

        if existing.status == CART_REQUEST_FILLING:
            raise CartRequestNotClearableError("the cart is already being filled; empty it in the store instead")

        self._write(shopping_list, dict.fromkeys(CART_REQUEST_EXTRAS_KEYS))

    def report(self, shopping_list_id: UUID4, data: CartRequestUpdate) -> CartRequestOut:
        """Move a request along: claim it, finish it, or fail it.

        The `request_id` must match the one the list is holding. Without that check a filler which
        started before a cancel-and-re-request could report "filled" onto the *new* request and the
        household would believe a shop had happened that never did.
        """

        shopping_list = self._require_list(shopping_list_id)

        existing = self.parse(shopping_list)
        if existing is None:
            raise NoCartRequestError("this list has no cart request")

        if existing.request_id != data.request_id:
            raise StaleCartRequestError(
                f"cart request {data.request_id} is no longer on this list; it now holds {existing.request_id}"
            )

        if data.status not in LEGAL_TRANSITIONS[existing.status]:
            raise IllegalCartRequestTransitionError(f"a {existing.status} cart request cannot become {data.status}")

        return self._write_request(
            shopping_list,
            {
                EXTRA_STATUS: data.status,
                EXTRA_UPDATED_AT: datetime.now(UTC).isoformat(),
                EXTRA_RESULT: data.result or None,
            },
        )

    # ---------------------------------------------------------------- helpers

    def _require_list(self, shopping_list_id: UUID4) -> ShoppingListOut:
        shopping_list = self.shopping_lists.get_one(shopping_list_id)
        if shopping_list is None:
            raise ShoppingListNotFoundError("shopping list not found")

        return shopping_list

    def _write_request(self, shopping_list: ShoppingListOut, changes: dict[str, str | None]) -> CartRequestOut:
        """`_write`, for changes that must leave a readable request behind.

        Raises rather than returning None: every caller of this has just written a status, so an
        unreadable read-back means the write did not do what this module thinks it did, and
        returning "no request" there would hand the UI a silent success.
        """

        written = self.parse(self._write(shopping_list, changes))
        if written is None:
            raise CartRequestError(f"cart request written to list {shopping_list.id} could not be read back")

        return written

    def _write(self, shopping_list: ShoppingListOut, changes: dict[str, str | None]) -> ShoppingListOut:
        """Merge `changes` into the list's extras, where a `None` value removes the key.

        Delegated to the repository, which knows how to touch the extras rows without walking the
        list's items or its recipe references. See `RepositoryShoppingList.set_extras`: the
        whole-object update path would delete every item on the list while writing one string.
        """

        return self.shopping_lists.set_extras(shopping_list.id, changes)
