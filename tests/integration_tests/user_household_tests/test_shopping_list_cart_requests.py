"""Integration tests for cart requests on a shopping list.

The feature exists so that somebody who is not the one with a browser signed into the grocery
store can still say "buy this". Four properties carry that weight and each has a test here: only
one fill can be owed at a time, an empty list cannot be sent, a late report cannot land on a
request that has since been replaced, and nothing about a request leaks to or from another
household.

The state lives in the shopping list's `extras`, which is a bag other integrations write to as
well, so there is also a test that making and clearing a request leaves the rest of that bag --
and every item on the list -- exactly as it was.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from mealie.schema.household.cart_request import (
    CART_REQUEST_EXTRAS_KEYS,
    EXTRA_REQUESTED_BY_NAME,
    EXTRA_STATUS,
)
from mealie.schema.household.group_shopping_list import ShoppingListOut
from tests.utils import api_routes
from tests.utils.assertion_helpers import assert_deserialize
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser


def cart_request_route(shopping_list_id) -> str:
    return api_routes.households_shopping_lists_item_id_cart_request(shopping_list_id)


def get_list(api_client: TestClient, user: TestUser, shopping_list_id) -> dict:
    response = api_client.get(api_routes.households_shopping_lists_item_id(shopping_list_id), headers=user.token)
    return assert_deserialize(response, 200)


def request_cart(api_client: TestClient, user: TestUser, shopping_list_id, expected_status: int = 201) -> dict:
    response = api_client.post(cart_request_route(shopping_list_id), headers=user.token)
    assert response.status_code == expected_status, response.text
    return response.json()


def report(api_client: TestClient, user: TestUser, shopping_list_id, **payload) -> tuple[int, dict]:
    response = api_client.put(cart_request_route(shopping_list_id), json=payload, headers=user.token)
    return response.status_code, response.json()


def poll(api_client: TestClient, user: TestUser, **params) -> list[dict]:
    response = api_client.get(api_routes.households_shopping_cart_requests, params=params, headers=user.token)
    return assert_deserialize(response, 200)["requests"]


def test_request_records_who_asked_and_how_much_was_left(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    created = request_cart(api_client, unique_user, list_with_items.id)

    assert created["status"] == "pending"
    assert created["shoppingListId"] == str(list_with_items.id)
    assert created["requestedBy"] == str(unique_user.user_id)
    assert created["requestedByName"] == unique_user.username
    assert created["itemCount"] == len([item for item in list_with_items.list_items if not item.checked])
    assert created["result"] is None

    # the request is readable straight off the list, which is what the UI and the poller both do
    extras = get_list(api_client, unique_user, list_with_items.id)["extras"]
    assert extras[EXTRA_STATUS] == "pending"
    assert extras[EXTRA_REQUESTED_BY_NAME] == unique_user.username


def test_second_request_is_refused_while_one_is_owed(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    first = request_cart(api_client, unique_user, list_with_items.id)

    refused = request_cart(api_client, unique_user, list_with_items.id, expected_status=409)
    assert "already has a cart request" in refused["detail"]["message"]

    # the refusal left the original alone rather than replacing it
    assert poll(api_client, unique_user)[0]["requestId"] == first["requestId"]


def test_a_claimed_request_also_blocks_a_second_one(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    created = request_cart(api_client, unique_user, list_with_items.id)
    status_code, _ = report(
        api_client, unique_user, list_with_items.id, requestId=created["requestId"], status="filling"
    )
    assert status_code == 200

    request_cart(api_client, unique_user, list_with_items.id, expected_status=409)


def test_request_is_refused_when_nothing_is_left_to_buy(
    api_client: TestClient, unique_user: TestUser, shopping_list: ShoppingListOut
):
    refused = request_cart(api_client, unique_user, shopping_list.id, expected_status=400)
    assert "ticked off" in refused["detail"]["message"]


def test_checked_items_do_not_count_towards_the_shop(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    """A list whose items are all ticked is an empty shop, even though it has items on it."""

    items = get_list(api_client, unique_user, list_with_items.id)["listItems"]
    for item in items[:-1]:
        item["checked"] = True
        response = api_client.put(
            api_routes.households_shopping_items_item_id(item["id"]), json=item, headers=unique_user.token
        )
        assert response.status_code == 200, response.text

    created = request_cart(api_client, unique_user, list_with_items.id)
    assert created["itemCount"] == 1


def test_cancelling_clears_the_request_and_frees_the_list(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    request_cart(api_client, unique_user, list_with_items.id)

    response = api_client.delete(cart_request_route(list_with_items.id), headers=unique_user.token)
    assert response.status_code == 200, response.text

    extras = get_list(api_client, unique_user, list_with_items.id)["extras"]
    assert not [key for key in CART_REQUEST_EXTRAS_KEYS if key in extras]
    assert poll(api_client, unique_user) == []

    # and the list can be sent again
    request_cart(api_client, unique_user, list_with_items.id)


def test_cancelling_is_refused_once_the_cart_is_being_filled(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    created = request_cart(api_client, unique_user, list_with_items.id)
    report(api_client, unique_user, list_with_items.id, requestId=created["requestId"], status="filling")

    response = api_client.delete(cart_request_route(list_with_items.id), headers=unique_user.token)
    assert response.status_code == 409, response.text
    assert "already being filled" in response.json()["detail"]["message"]

    # still filling: the refusal did not quietly clear it
    assert poll(api_client, unique_user)[0]["status"] == "filling"


def test_a_finished_request_can_be_dismissed(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    created = request_cart(api_client, unique_user, list_with_items.id)
    report(api_client, unique_user, list_with_items.id, requestId=created["requestId"], status="filled", result="ok")

    response = api_client.delete(cart_request_route(list_with_items.id), headers=unique_user.token)
    assert response.status_code == 200, response.text
    assert EXTRA_STATUS not in get_list(api_client, unique_user, list_with_items.id)["extras"]


def test_cancelling_nothing_is_a_404(api_client: TestClient, unique_user: TestUser, shopping_list: ShoppingListOut):
    response = api_client.delete(cart_request_route(shopping_list.id), headers=unique_user.token)
    assert response.status_code == 404, response.text


def test_report_carries_the_outcome_back_to_the_household(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    created = request_cart(api_client, unique_user, list_with_items.id)

    status_code, claimed = report(
        api_client, unique_user, list_with_items.id, requestId=created["requestId"], status="filling"
    )
    assert status_code == 200, claimed
    assert claimed["status"] == "filling"
    assert claimed["requestId"] == created["requestId"]
    assert claimed["requestedByName"] == unique_user.username

    summary = "9 in the cart, no organic peaches"
    status_code, filled = report(
        api_client, unique_user, list_with_items.id, requestId=created["requestId"], status="filled", result=summary
    )
    assert status_code == 200, filled
    assert filled["status"] == "filled"
    assert filled["result"] == summary
    assert filled["updatedAt"] > filled["requestedAt"]

    # the item count is the snapshot taken when the request was made, not a live recount
    assert filled["itemCount"] == created["itemCount"]


@pytest.mark.parametrize("bad_status", ["pending", "cancelled", "", "FILLED"])
def test_report_refuses_a_status_outside_the_vocabulary(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut, bad_status: str
):
    created = request_cart(api_client, unique_user, list_with_items.id)

    status_code, _ = report(
        api_client, unique_user, list_with_items.id, requestId=created["requestId"], status=bad_status
    )
    assert status_code == 422

    assert poll(api_client, unique_user)[0]["status"] == "pending"


def test_report_on_a_replaced_request_is_refused(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    """The exact failure this check exists for: a fill that started before a cancel-and-re-ask must
    not be able to stamp "filled" onto the request nobody has acted on yet."""

    stale = request_cart(api_client, unique_user, list_with_items.id)
    api_client.delete(cart_request_route(list_with_items.id), headers=unique_user.token)
    current = request_cart(api_client, unique_user, list_with_items.id)
    assert current["requestId"] != stale["requestId"]

    status_code, refused = report(
        api_client, unique_user, list_with_items.id, requestId=stale["requestId"], status="filled", result="bought it"
    )
    assert status_code == 409, refused
    assert "no longer on this list" in refused["detail"]["message"]

    live = poll(api_client, unique_user)[0]
    assert live["requestId"] == current["requestId"]
    assert live["status"] == "pending"
    assert live["result"] is None


def test_a_finished_request_cannot_be_reported_on_again(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    created = request_cart(api_client, unique_user, list_with_items.id)
    report(api_client, unique_user, list_with_items.id, requestId=created["requestId"], status="filled", result="done")

    status_code, refused = report(
        api_client,
        unique_user,
        list_with_items.id,
        requestId=created["requestId"],
        status="failed",
        result="actually no",
    )
    assert status_code == 409, refused
    assert "cannot become" in refused["detail"]["message"]


def test_report_without_a_request_is_a_404(
    api_client: TestClient, unique_user: TestUser, shopping_list: ShoppingListOut
):
    status_code, _ = report(
        api_client,
        unique_user,
        shopping_list.id,
        requestId=str(uuid4()),
        status="filled",
    )
    assert status_code == 404


def test_poll_returns_what_is_still_owed_oldest_first(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut, shopping_lists
):
    """Two requests in the household come back in the order they were asked for."""

    first = request_cart(api_client, unique_user, list_with_items.id)

    second_list = shopping_lists[0]
    api_client.post(
        api_routes.households_shopping_items,
        json={"shoppingListId": str(second_list.id), "note": random_string(10), "quantity": 1, "isFood": False},
        headers=unique_user.token,
    )
    second = request_cart(api_client, unique_user, second_list.id)

    waiting = poll(api_client, unique_user)
    assert [r["requestId"] for r in waiting] == [first["requestId"], second["requestId"]]
    assert [r["shoppingListName"] for r in waiting] == [list_with_items.name, second_list.name]


def test_poll_hides_finished_requests_unless_asked(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    created = request_cart(api_client, unique_user, list_with_items.id)
    report(api_client, unique_user, list_with_items.id, requestId=created["requestId"], status="filled", result="done")

    assert poll(api_client, unique_user) == []

    filled = poll(api_client, unique_user, status="filled")
    assert [r["requestId"] for r in filled] == [created["requestId"]]


def test_poll_refuses_a_status_it_does_not_know(api_client: TestClient, unique_user: TestUser):
    response = api_client.get(
        api_routes.households_shopping_cart_requests, params={"status": "shopping"}, headers=unique_user.token
    )
    assert response.status_code == 422, response.text


def test_either_person_in_the_household_can_see_and_settle_the_request(
    api_client: TestClient, user_tuple: list[TestUser]
):
    """The household shape this exists for: one person asks, the other one (or something holding
    their token) is the one who finishes it."""

    asker, housemate = user_tuple

    created_list = assert_deserialize(
        api_client.post(api_routes.households_shopping_lists, json={"name": random_string(10)}, headers=asker.token),
        201,
    )
    api_client.post(
        api_routes.households_shopping_items,
        json={"shoppingListId": created_list["id"], "note": random_string(10), "quantity": 1, "isFood": False},
        headers=asker.token,
    )

    created = request_cart(api_client, asker, created_list["id"])

    seen = [r for r in poll(api_client, housemate) if r["shoppingListId"] == created_list["id"]]
    assert [r["requestId"] for r in seen] == [created["requestId"]]
    assert seen[0]["requestedByName"] == asker.username

    status_code, claimed = report(
        api_client, housemate, created_list["id"], requestId=created["requestId"], status="filling"
    )
    assert status_code == 200, claimed
    assert claimed["requestedByName"] == asker.username

    status_code, filled = report(
        api_client, housemate, created_list["id"], requestId=created["requestId"], status="filled", result="done"
    )
    assert status_code == 200, filled

    assert api_client.delete(cart_request_route(created_list["id"]), headers=housemate.token).status_code == 200


def test_another_household_cannot_see_or_touch_the_request(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, list_with_items: ShoppingListOut
):
    created = request_cart(api_client, unique_user, list_with_items.id)

    assert poll(api_client, h2_user) == []

    assert api_client.post(cart_request_route(list_with_items.id), headers=h2_user.token).status_code == 404
    assert api_client.delete(cart_request_route(list_with_items.id), headers=h2_user.token).status_code == 404
    status_code, _ = report(
        api_client, h2_user, list_with_items.id, requestId=created["requestId"], status="filled", result="mine now"
    )
    assert status_code == 404

    # and the owner's request is untouched by any of it
    assert poll(api_client, unique_user)[0]["status"] == "pending"


def test_a_request_leaves_the_list_and_its_other_extras_alone(
    api_client: TestClient, unique_user: TestUser, list_with_items: ShoppingListOut
):
    """Extras is shared with every other integration, and `list_items` is the list itself."""

    before = get_list(api_client, unique_user, list_with_items.id)
    before["extras"] = {"kitchenDashboard": "pinned", "cartRequestStatusLookalike": "not ours"}
    response = api_client.put(
        api_routes.households_shopping_lists_item_id(list_with_items.id), json=before, headers=unique_user.token
    )
    assert response.status_code == 200, response.text

    created = request_cart(api_client, unique_user, list_with_items.id)
    during = get_list(api_client, unique_user, list_with_items.id)
    assert during["extras"]["kitchenDashboard"] == "pinned"
    assert during["extras"]["cartRequestStatusLookalike"] == "not ours"
    assert len(during["listItems"]) == len(before["listItems"])
    assert {item["id"] for item in during["listItems"]} == {item["id"] for item in before["listItems"]}

    report(api_client, unique_user, list_with_items.id, requestId=created["requestId"], status="filled", result="done")
    api_client.delete(cart_request_route(list_with_items.id), headers=unique_user.token)

    after = get_list(api_client, unique_user, list_with_items.id)
    assert after["extras"]["kitchenDashboard"] == "pinned"
    assert after["extras"]["cartRequestStatusLookalike"] == "not ours"
    assert len(after["listItems"]) == len(before["listItems"])
