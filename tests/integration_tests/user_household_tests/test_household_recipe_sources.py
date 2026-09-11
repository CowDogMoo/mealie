"""Integration tests for the household recipe source list.

The list is keyed on a normalized domain and scoped to the household, so two properties are
load-bearing here: what a person pastes (a full URL, `www.`, capitals) collapses to one key, and a
member of a *different* household in the same group sees none of it, through any route.

Entries are created through the API and deleted in the fixture teardown, so exact-count
assertions stay honest even though `unique_user` and `h2_user` are module-scoped.
"""

import contextlib
from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient

from tests.utils import api_routes
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser

SourceFactory = Callable[..., dict]


@pytest.fixture(scope="function")
def source_factory(api_client: TestClient) -> Generator[SourceFactory, None, None]:
    """Create a source through the API for a given user and delete it when the test ends."""

    created: list[tuple[TestUser, str]] = []

    def create(user: TestUser, domain: str | None = None, **payload) -> dict:
        body = {"domain": domain or f"{random_string(8)}.example", **payload}
        response = api_client.post(api_routes.households_recipe_sources, json=body, headers=user.token)
        assert response.status_code == 201, response.text
        data = response.json()
        created.append((user, data["id"]))
        return data

    yield create

    for user, item_id in created:
        with contextlib.suppress(Exception):
            api_client.delete(api_routes.households_recipe_sources_item_id(item_id), headers=user.token)


def list_sources(api_client: TestClient, user: TestUser) -> list[dict]:
    response = api_client.get(api_routes.households_recipe_sources, params={"perPage": -1}, headers=user.token)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def lookup(api_client: TestClient, user: TestUser, url: str) -> dict:
    response = api_client.get(api_routes.households_recipe_sources_lookup, params={"url": url}, headers=user.token)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# create and normalization


def test_create_defaults_to_known_good(api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory):
    created = source_factory(unique_user, domain="budgetbytes.com")

    assert created["domain"] == "budgetbytes.com"
    assert created["status"] == "known-good"
    assert created["note"] is None
    assert created["householdId"] == unique_user.household_id
    assert created["groupId"] == unique_user.group_id


@pytest.mark.parametrize(
    "pasted",
    [
        "https://www.BudgetBytes.com/easy-chicken/",
        "http://budgetbytes.com:8080/recipes?x=1",
        "WWW.budgetbytes.com",
        "budgetbytes.com.",
        "user:pass@budgetbytes.com/x",
    ],
)
def test_create_normalizes_url_to_domain(
    api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory, pasted: str
):
    created = source_factory(unique_user, domain=pasted)
    assert created["domain"] == "budgetbytes.com"


def test_create_trims_note_and_blank_note_is_null(
    api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory
):
    with_note = source_factory(unique_user, note="  clean weeknight recipes  ")
    assert with_note["note"] == "clean weeknight recipes"

    blank = source_factory(unique_user, note="   ")
    assert blank["note"] is None


@pytest.mark.parametrize("domain", ["", "   ", "https://", "..", "a..b"])
def test_create_rejects_unusable_domain(api_client: TestClient, unique_user: TestUser, domain: str):
    response = api_client.post(api_routes.households_recipe_sources, json={"domain": domain}, headers=unique_user.token)
    assert response.status_code == 422, response.text


def test_create_rejects_unknown_status(api_client: TestClient, unique_user: TestUser):
    response = api_client.post(
        api_routes.households_recipe_sources,
        json={"domain": "example.com", "status": "trusted"},
        headers=unique_user.token,
    )
    assert response.status_code == 422, response.text


def test_create_rejects_duplicate_domain_in_any_spelling(
    api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory
):
    source_factory(unique_user, domain="onceuponachef.com")

    response = api_client.post(
        api_routes.households_recipe_sources,
        json={"domain": "https://WWW.onceuponachef.com/recipes/x.html", "status": "blocked"},
        headers=unique_user.token,
    )
    assert response.status_code == 409, response.text
    assert "onceuponachef.com" in response.json()["detail"]["message"]

    # the rejected write changed nothing
    sources = [s for s in list_sources(api_client, unique_user) if s["domain"] == "onceuponachef.com"]
    assert len(sources) == 1
    assert sources[0]["status"] == "known-good"


# ---------------------------------------------------------------------------
# read, update, delete


def test_list_get_update_delete_roundtrip(api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory):
    created = source_factory(unique_user, domain="skinnytaste.com", status="caution", note="verify counts")
    item_id = created["id"]

    listed = {s["id"]: s for s in list_sources(api_client, unique_user)}
    assert listed[item_id]["domain"] == "skinnytaste.com"
    assert listed[item_id]["status"] == "caution"

    response = api_client.get(api_routes.households_recipe_sources_item_id(item_id), headers=unique_user.token)
    assert response.status_code == 200, response.text
    assert response.json()["note"] == "verify counts"

    response = api_client.put(
        api_routes.households_recipe_sources_item_id(item_id),
        json={"domain": "https://www.skinnytaste.com/", "status": "known-good", "note": ""},
        headers=unique_user.token,
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["id"] == item_id
    assert updated["domain"] == "skinnytaste.com"
    assert updated["status"] == "known-good"
    assert updated["note"] is None

    response = api_client.delete(api_routes.households_recipe_sources_item_id(item_id), headers=unique_user.token)
    assert response.status_code == 200, response.text

    response = api_client.get(api_routes.households_recipe_sources_item_id(item_id), headers=unique_user.token)
    assert response.status_code == 404, response.text


def test_update_cannot_take_another_entrys_domain(
    api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory
):
    source_factory(unique_user, domain="pinchofyum.com")
    other = source_factory(unique_user, domain="cookieandkate.com")

    response = api_client.put(
        api_routes.households_recipe_sources_item_id(other["id"]),
        json={"domain": "pinchofyum.com", "status": "known-good"},
        headers=unique_user.token,
    )
    assert response.status_code == 409, response.text

    # renaming to its own domain is not a conflict
    response = api_client.put(
        api_routes.households_recipe_sources_item_id(other["id"]),
        json={"domain": "www.cookieandkate.com", "status": "blocked"},
        headers=unique_user.token,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"


def test_list_orders_and_filters_by_column(
    api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory
):
    """The page asks for `orderBy=domain`; a column that is not filterable answers 400 to that."""

    source_factory(unique_user, domain="zzz-last.example", status="blocked")
    source_factory(unique_user, domain="aaa-first.example", status="known-good")

    response = api_client.get(
        api_routes.households_recipe_sources,
        params={"perPage": -1, "orderBy": "domain", "orderDirection": "asc"},
        headers=unique_user.token,
    )
    assert response.status_code == 200, response.text
    domains = [
        s["domain"]
        for s in response.json()["items"]
        if s["domain"].endswith("-first.example") or s["domain"].endswith("-last.example")
    ]
    assert domains == ["aaa-first.example", "zzz-last.example"]

    response = api_client.get(
        api_routes.households_recipe_sources,
        params={"perPage": -1, "queryFilter": 'status = "blocked"'},
        headers=unique_user.token,
    )
    assert response.status_code == 200, response.text
    assert {s["status"] for s in response.json()["items"]} == {"blocked"}


# ---------------------------------------------------------------------------
# lookup


def test_lookup_exact_domain(api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory):
    created = source_factory(unique_user, domain="damndelicious.net", status="caution", note="403s curl")

    found = lookup(api_client, unique_user, "https://www.damndelicious.net/2024/01/01/thai-basil-chicken/")
    assert found["domain"] == "damndelicious.net"
    assert found["status"] == "caution"
    assert found["source"]["id"] == created["id"]
    assert found["source"]["note"] == "403s curl"


def test_lookup_parent_domain_covers_subdomain(
    api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory
):
    created = source_factory(unique_user, domain="nytimes.com", status="known-good")

    found = lookup(api_client, unique_user, "https://cooking.nytimes.com/recipes/1/x")
    assert found["domain"] == "cooking.nytimes.com"
    assert found["status"] == "known-good"
    assert found["source"]["id"] == created["id"]
    assert found["source"]["domain"] == "nytimes.com"


def test_lookup_specific_entry_beats_parent(
    api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory
):
    source_factory(unique_user, domain="bonappetit.com", status="known-good")
    specific = source_factory(unique_user, domain="forums.bonappetit.com", status="blocked")

    found = lookup(api_client, unique_user, "https://forums.bonappetit.com/thread/1")
    assert found["status"] == "blocked"
    assert found["source"]["id"] == specific["id"]

    # a sibling subdomain still falls through to the parent
    found = lookup(api_client, unique_user, "https://www.bonappetit.com/recipe/x")
    assert found["status"] == "known-good"


def test_lookup_unlisted_domain_answers_null_status(api_client: TestClient, unique_user: TestUser):
    found = lookup(api_client, unique_user, f"https://{random_string(10)}.example/recipe")
    assert found["status"] is None
    assert found["source"] is None


def test_lookup_never_matches_a_bare_tld(api_client: TestClient, unique_user: TestUser, source_factory: SourceFactory):
    # a positive control for the walk: the entry exists, but a lookup must stop before the TLD
    source_factory(unique_user, domain="com.example", status="blocked")

    found = lookup(api_client, unique_user, "https://something.example/recipe")
    assert found["status"] is None


@pytest.mark.parametrize("url", ["", "   ", "https://", "a..b"])
def test_lookup_rejects_unusable_url(api_client: TestClient, unique_user: TestUser, url: str):
    response = api_client.get(
        api_routes.households_recipe_sources_lookup, params={"url": url}, headers=unique_user.token
    )
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# household boundary


def test_other_household_in_same_group_sees_nothing(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, source_factory: SourceFactory
):
    assert unique_user.group_id == h2_user.group_id
    assert unique_user.household_id != h2_user.household_id

    mine = source_factory(unique_user, domain="halfbakedharvest.com", status="blocked")

    assert all(s["id"] != mine["id"] for s in list_sources(api_client, h2_user))

    found = lookup(api_client, h2_user, "https://www.halfbakedharvest.com/x/")
    assert found["status"] is None

    response = api_client.get(api_routes.households_recipe_sources_item_id(mine["id"]), headers=h2_user.token)
    assert response.status_code == 404, response.text

    response = api_client.put(
        api_routes.households_recipe_sources_item_id(mine["id"]),
        json={"domain": "halfbakedharvest.com", "status": "known-good"},
        headers=h2_user.token,
    )
    assert response.status_code == 404, response.text

    response = api_client.delete(api_routes.households_recipe_sources_item_id(mine["id"]), headers=h2_user.token)
    assert response.status_code == 404, response.text

    # and the entry is untouched by any of that
    found = lookup(api_client, unique_user, "https://halfbakedharvest.com/")
    assert found["status"] == "blocked"


def test_same_domain_may_be_listed_by_each_household(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, source_factory: SourceFactory
):
    mine = source_factory(unique_user, domain="themodernproper.com", status="known-good")
    theirs = source_factory(h2_user, domain="themodernproper.com", status="blocked")

    assert mine["id"] != theirs["id"]
    assert lookup(api_client, unique_user, "https://themodernproper.com/x")["status"] == "known-good"
    assert lookup(api_client, h2_user, "https://themodernproper.com/x")["status"] == "blocked"
