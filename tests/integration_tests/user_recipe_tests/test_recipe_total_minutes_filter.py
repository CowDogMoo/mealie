"""Filtering the recipe list by cook time, over the API, the way the browse grid does.

This file exists because of a specific trap: a column the UI filters or orders by
has to be declared `FilterableColumn[...]` rather than `Mapped[...]`, or the
endpoint answers 400 "Cannot filter on Model.column" — and a test only catches it
if it actually sends `queryFilter` and `orderBy`. These do.

Every absence assertion here is paired with the matching presence in the same
run. A filter that returned nothing at all would satisfy "the long recipes are
not in the weeknight results" perfectly, so that alone would prove nothing.
"""

import pytest
from fastapi.testclient import TestClient

from mealie.schema.recipe.recipe import Recipe
from tests.utils import api_routes
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser

# (label, total_time as a person or a scraper would write it, minutes it must parse to)
LIBRARY = [
    ("weeknight-20", "20 minutes", 20),
    ("weeknight-30", "PT30M", 30),
    ("weeknight-44", "44 minutes", 44),
    ("band-45", "45 minutes", 45),
    ("band-50", "50 min", 50),
    ("band-60", "1 hour", 60),
    ("long-61", "61 minutes", 61),
    ("long-75", "1 Hour 15 Minutes", 75),
    ("long-125", "PT2H5M", 125),
    ("untimed", "some evening", None),
]

WEEKNIGHT = {"weeknight-20", "weeknight-30", "weeknight-44"}
BAND = {"band-45", "band-50", "band-60"}
TOO_LONG = {"long-61", "long-75", "long-125"}
UNTIMED = {"untimed"}


@pytest.fixture(scope="module")
def cook_time_library(unique_user: TestUser) -> dict[str, str]:
    """One recipe per cook-time form, named so a result set is readable in a failure.

    Module-scoped to match `unique_user`. Function scope under a module-scoped
    user built a fresh library per test against the same account, so the last
    test in the file read eight tests' worth of recipes and the size of the
    result set depended on which tests ran. Every test here only reads, so one
    library serves all of them.
    """
    database = unique_user.repos
    slugs: dict[str, str] = {}
    prefix = random_string(8)

    for label, total_time, expected_minutes in LIBRARY:
        recipe = database.recipes.create(
            Recipe(
                user_id=unique_user.user_id,
                group_id=unique_user.group_id,
                name=f"{prefix} {label}",
                total_time=total_time,
            )
        )
        assert recipe.total_minutes == expected_minutes, (
            f"{label}: {total_time!r} stored as {recipe.total_minutes!r}, expected {expected_minutes!r}"
        )
        slugs[label] = recipe.slug

    return {"prefix": prefix, **slugs}


def labels_in(response_json, prefix: str) -> set[str]:
    """The labels of our own recipes in a page of results, ignoring anything else."""
    return {
        item["name"].removeprefix(f"{prefix} ") for item in response_json["items"] if item["name"].startswith(prefix)
    }


def query(api_client: TestClient, unique_user: TestUser, query_filter: str, **extra) -> dict:
    response = api_client.get(
        api_routes.recipes,
        params={"perPage": -1, "queryFilter": query_filter, **extra},
        headers=unique_user.token,
    )
    # A 400 here is the FilterableColumn trap: the column exists and holds the
    # right numbers, and the endpoint still refuses to filter on it.
    assert response.status_code == 200, response.text
    return response.json()


def test_the_whole_library_is_reachable_unfiltered(
    api_client: TestClient, unique_user: TestUser, cook_time_library: dict
):
    """The control every absence assertion below leans on.

    If this fails, "the long ones are not in the weeknight results" means only
    that the recipes were never created.
    """
    prefix = cook_time_library["prefix"]
    response = api_client.get(api_routes.recipes, params={"perPage": -1}, headers=unique_user.token)
    assert response.status_code == 200, response.text
    assert labels_in(response.json(), prefix) == WEEKNIGHT | BAND | TOO_LONG | UNTIMED


def test_weeknight_filter_returns_only_weeknight_lengths(
    api_client: TestClient, unique_user: TestUser, cook_time_library: dict
):
    prefix = cook_time_library["prefix"]
    found = labels_in(query(api_client, unique_user, "totalMinutes < 45"), prefix)

    assert found == WEEKNIGHT
    assert not found & BAND, "a band recipe came back as a weeknight dinner"
    assert not found & TOO_LONG, "a recipe past the ceiling came back as a weeknight dinner"
    assert not found & UNTIMED, "a recipe with no readable cook time was treated as short"


def test_band_filter_is_inclusive_at_both_edges(api_client: TestClient, unique_user: TestUser, cook_time_library: dict):
    """45 and 60 are both in the band, matching the `45-60` tag to the minute."""
    prefix = cook_time_library["prefix"]
    found = labels_in(query(api_client, unique_user, "totalMinutes >= 45 AND totalMinutes <= 60"), prefix)

    assert found == BAND
    assert "band-45" in found and "band-60" in found
    assert "weeknight-44" not in found and "long-61" not in found


def test_too_long_filter_returns_only_what_is_past_the_ceiling(
    api_client: TestClient, unique_user: TestUser, cook_time_library: dict
):
    prefix = cook_time_library["prefix"]
    found = labels_in(query(api_client, unique_user, "totalMinutes > 60"), prefix)

    assert found == TOO_LONG
    assert not found & WEEKNIGHT
    assert not found & BAND


def test_a_recipe_with_no_readable_cook_time_is_findable_rather_than_lost(
    api_client: TestClient, unique_user: TestUser, cook_time_library: dict
):
    """It belongs to no length filter, and it is not invisible either.

    NULL is the honest answer for "some evening", but a recipe you can never
    surface is a recipe you cannot fix.
    """
    prefix = cook_time_library["prefix"]
    assert labels_in(query(api_client, unique_user, "totalMinutes IS NULL"), prefix) == UNTIMED


def test_the_filter_holds_across_pages(api_client: TestClient, unique_user: TestUser, cook_time_library: dict):
    """Paginated, not just filtered on the first page.

    Client-side filtering of a lazily loaded grid passes every test above and
    still shows the wrong thing on page two, which is the reason this is a column
    and not a computed property.
    """
    prefix = cook_time_library["prefix"]
    collected: set[str] = set()
    page = 1
    while True:
        response = api_client.get(
            api_routes.recipes,
            params={"page": page, "perPage": 2, "queryFilter": "totalMinutes > 60", "orderBy": "totalMinutes"},
            headers=unique_user.token,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        collected |= labels_in(body, prefix)
        if page >= body["total_pages"] or not body["items"]:
            break
        page += 1

    assert page > 1, "perPage=2 over three matches should have needed more than one page"
    assert collected == TOO_LONG


def test_ordering_by_cook_time_is_numeric_not_alphabetical(
    api_client: TestClient, unique_user: TestUser, cook_time_library: dict
):
    """The reason the column is an integer.

    Ordered as text, "125 minutes" sorts before "20 minutes". This is also the
    other half of the FilterableColumn trap, which `orderBy` triggers too.
    """
    prefix = cook_time_library["prefix"]

    def ordered(direction: str) -> list[int]:
        response = api_client.get(
            api_routes.recipes,
            params={
                "perPage": -1,
                "queryFilter": "totalMinutes IS NOT NULL",
                "orderBy": "totalMinutes",
                "orderDirection": direction,
            },
            headers=unique_user.token,
        )
        assert response.status_code == 200, response.text
        return [item["totalMinutes"] for item in response.json()["items"] if item["name"].startswith(prefix)]

    ascending = ordered("asc")
    assert ascending == sorted(ascending), f"cook times came back out of order: {ascending}"
    assert ascending[0] == 20 and ascending[-1] == 125

    # Both directions, because either one alone can be satisfied by an accident
    # of insertion order. Sorted as text, "125" would lead ascending and "75"
    # would lead descending; sorted as numbers it is 20 and 125.
    descending = ordered("desc")
    assert descending == sorted(descending, reverse=True), f"cook times came back out of order: {descending}"
    assert descending[0] == 125 and descending[-1] == 20


def test_the_api_reports_the_parsed_length_on_each_recipe(
    api_client: TestClient, unique_user: TestUser, cook_time_library: dict
):
    """The badge reads this field, so it has to arrive on the list response."""
    prefix = cook_time_library["prefix"]
    response = api_client.get(api_routes.recipes, params={"perPage": -1}, headers=unique_user.token)
    assert response.status_code == 200, response.text

    by_label = {
        item["name"].removeprefix(f"{prefix} "): item
        for item in response.json()["items"]
        if item["name"].startswith(prefix)
    }
    for label, _total_time, expected in LIBRARY:
        assert by_label[label]["totalMinutes"] == expected
