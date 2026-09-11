"""The two places the household recipe source list acts on its own.

1. The URL importer consults it before fetching anything: a blocked site is refused, a site on
   caution or one the list has never heard of gets a progress warning, a known-good site is silent.
2. A licensed `bad-source` vote demotes the recipe's site to caution.

The scraper is replaced by a stub that returns a bare recipe, so these tests never touch the
network and never depend on the HTML fixtures the scraper tests use. What is under test is the
gate around the scraper, not the scraper.
"""

import contextlib
from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from mealie.schema.recipe.recipe import Recipe
from tests.utils import api_routes
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser
from tests.utils.helpers import parse_sse_events

SOURCE_LIST_PHRASE = "recipe source list"
"""Every source-list message the importer emits names the list; no other importer message does."""


@pytest.fixture(scope="function")
def stub_scraper(monkeypatch: MonkeyPatch) -> list[str]:
    """Replace `create_from_html` with a stub that records the URLs it was asked to scrape."""

    scraped: list[str] = []

    async def fake_create_from_html(url: str, repos, translator, html=None, on_progress=None, **_) -> tuple:
        scraped.append(url)
        if on_progress is not None:
            await on_progress("scraping")
        slug = random_string(12)
        return Recipe(name=slug, slug=slug, org_url=url), None

    monkeypatch.setattr("mealie.routes.recipe.recipe_crud_routes.create_from_html", fake_create_from_html)
    return scraped


@pytest.fixture(scope="function")
def cleanup(api_client: TestClient, unique_user: TestUser) -> Generator[None, None, None]:
    """Remove every source-list entry and every recipe the test left behind in `unique_user`'s household."""

    yield

    response = api_client.get(api_routes.households_recipe_sources, params={"perPage": -1}, headers=unique_user.token)
    for source in response.json().get("items", []):
        with contextlib.suppress(Exception):
            api_client.delete(api_routes.households_recipe_sources_item_id(source["id"]), headers=unique_user.token)


def add_source(api_client: TestClient, user: TestUser, domain: str, status: str, note: str | None = None) -> dict:
    response = api_client.post(
        api_routes.households_recipe_sources,
        json={"domain": domain, "status": status, "note": note},
        headers=user.token,
    )
    assert response.status_code == 201, response.text
    return response.json()


def sources_by_domain(api_client: TestClient, user: TestUser) -> dict[str, dict]:
    response = api_client.get(api_routes.households_recipe_sources, params={"perPage": -1}, headers=user.token)
    assert response.status_code == 200, response.text
    return {s["domain"]: s for s in response.json()["items"]}


def stream_import(api_client: TestClient, user: TestUser, url: str) -> list[dict]:
    response = api_client.post(api_routes.recipes_create_url_stream, json={"url": url}, headers=user.token)
    assert response.status_code == 200, response.text
    return parse_sse_events(response.text)


def source_messages(events: list[dict]) -> list[str]:
    return [
        e["data"]["message"] for e in events if e["event"] == "progress" and SOURCE_LIST_PHRASE in e["data"]["message"]
    ]


def delete_recipe(api_client: TestClient, user: TestUser, slug: str) -> None:
    with contextlib.suppress(Exception):
        api_client.delete(api_routes.recipes_slug(slug), headers=user.token)


# ---------------------------------------------------------------------------
# import gate


def test_blocked_domain_refuses_url_import_before_scraping(
    api_client: TestClient, unique_user: TestUser, stub_scraper: list[str], cleanup: None
):
    add_source(api_client, unique_user, "allrecipes.com", "blocked", note="blocked from search here")

    response = api_client.post(
        api_routes.recipes_create_url,
        json={"url": "https://www.allrecipes.com/recipe/12345/x/"},
        headers=unique_user.token,
    )
    assert response.status_code == 400, response.text
    message = response.json()["detail"]["message"]
    assert "allrecipes.com" in message
    assert "blocked from search here" in message

    assert stub_scraper == [], "the scraper must not run for a blocked site"


def test_blocked_domain_refuses_streamed_import(
    api_client: TestClient, unique_user: TestUser, stub_scraper: list[str], cleanup: None
):
    add_source(api_client, unique_user, "foodnetwork.com", "blocked")

    events = stream_import(api_client, unique_user, "https://foodnetwork.com/recipes/x")
    assert [e["event"] for e in events] == ["error"]
    assert "foodnetwork.com" in events[0]["data"]["message"]
    assert stub_scraper == []


def test_caution_domain_warns_then_imports(
    api_client: TestClient, unique_user: TestUser, stub_scraper: list[str], cleanup: None
):
    add_source(api_client, unique_user, "seriouseats.com", "caution", note="ratings inconsistent")
    url = "https://www.seriouseats.com/best-chili"

    events = stream_import(api_client, unique_user, url)
    try:
        warnings = source_messages(events)
        assert len(warnings) == 1
        assert "seriouseats.com" in warnings[0]
        assert "ratings inconsistent" in warnings[0]

        # the warning is the first thing said, before the scraper does anything
        progress = [e for e in events if e["event"] == "progress"]
        assert progress[0]["data"]["message"] == warnings[0]

        assert events[-1]["event"] == "done"
        assert stub_scraper == [url]
    finally:
        delete_recipe(api_client, unique_user, events[-1]["data"].get("slug", ""))


def test_unlisted_domain_warns_then_imports(
    api_client: TestClient, unique_user: TestUser, stub_scraper: list[str], cleanup: None
):
    url = f"https://{random_string(10)}.example/recipe"

    events = stream_import(api_client, unique_user, url)
    try:
        warnings = source_messages(events)
        assert len(warnings) == 1
        assert "not on" in warnings[0]
        assert events[-1]["event"] == "done"
        assert stub_scraper == [url]
    finally:
        delete_recipe(api_client, unique_user, events[-1]["data"].get("slug", ""))


def test_known_good_domain_imports_silently(
    api_client: TestClient, unique_user: TestUser, stub_scraper: list[str], cleanup: None
):
    add_source(api_client, unique_user, "budgetbytes.com", "known-good")
    url = "https://www.budgetbytes.com/one-pot-pasta/"

    events = stream_import(api_client, unique_user, url)
    try:
        assert source_messages(events) == []
        assert events[-1]["event"] == "done"
        assert stub_scraper == [url]
    finally:
        delete_recipe(api_client, unique_user, events[-1]["data"].get("slug", ""))


def test_parent_entry_governs_subdomain_import(
    api_client: TestClient, unique_user: TestUser, stub_scraper: list[str], cleanup: None
):
    add_source(api_client, unique_user, "example-network.com", "blocked")

    response = api_client.post(
        api_routes.recipes_create_url,
        json={"url": "https://recipes.example-network.com/x"},
        headers=unique_user.token,
    )
    assert response.status_code == 400, response.text
    assert stub_scraper == []


def test_other_households_list_does_not_govern_my_import(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, stub_scraper: list[str], cleanup: None
):
    add_source(api_client, unique_user, "shared-group-site.com", "blocked")
    url = "https://shared-group-site.com/recipe"

    events = stream_import(api_client, h2_user, url)
    try:
        assert events[-1]["event"] == "done"
        assert stub_scraper == [url]
    finally:
        delete_recipe(api_client, h2_user, events[-1]["data"].get("slug", ""))


# ---------------------------------------------------------------------------
# bad-source demotion

RecipeFactory = Callable[..., Recipe]


@pytest.fixture(scope="function")
def recipe_factory(unique_user: TestUser) -> Generator[RecipeFactory, None, None]:
    created: list[Recipe] = []

    def create(org_url: str | None) -> Recipe:
        slug = random_string(12)
        recipe = unique_user.repos.recipes.create(
            Recipe(user_id=unique_user.user_id, group_id=unique_user.group_id, name=slug, slug=slug, org_url=org_url)
        )
        created.append(recipe)
        return recipe

    yield create

    for recipe in created:
        with contextlib.suppress(Exception):
            unique_user.repos.recipes.delete(recipe.id, match_key="id")
            unique_user.repos.session.commit()


def post_feedback(api_client: TestClient, user: TestUser, recipe: Recipe, **payload) -> dict:
    response = api_client.post(
        api_routes.users_id_feedback_slug(user.user_id, recipe.slug), json=payload, headers=user.token
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_bad_source_vote_lists_unlisted_site_as_caution(
    api_client: TestClient, unique_user: TestUser, recipe_factory: RecipeFactory, cleanup: None
):
    recipe = recipe_factory("https://www.some-blog.example/recipes/chili/")

    event = post_feedback(
        api_client, unique_user, recipe, vote="down", reason="bad-source", scope="source", target="some blog"
    )
    assert event["advisory"] is False

    entry = sources_by_domain(api_client, unique_user)["some-blog.example"]
    assert entry["status"] == "caution"
    assert unique_user.username in entry["note"]
    assert recipe.name in entry["note"]


def test_bad_source_vote_demotes_known_good_and_keeps_the_note(
    api_client: TestClient, unique_user: TestUser, recipe_factory: RecipeFactory, cleanup: None
):
    add_source(api_client, unique_user, "trusted.example", "known-good", note="tier 1")
    recipe = recipe_factory("https://trusted.example/x")

    post_feedback(api_client, unique_user, recipe, vote="down", reason="bad-source", scope="source", target="trusted")

    entry = sources_by_domain(api_client, unique_user)["trusted.example"]
    assert entry["status"] == "caution"
    assert entry["note"].startswith("tier 1\n")
    assert "bad-source vote" in entry["note"]


def test_second_bad_source_vote_keeps_one_caution_entry(
    api_client: TestClient, unique_user: TestUser, recipe_factory: RecipeFactory, cleanup: None
):
    recipe = recipe_factory("https://repeat.example/a")
    other = recipe_factory("https://www.repeat.example/b")

    post_feedback(api_client, unique_user, recipe, vote="down", reason="bad-source", scope="source", target="r")
    first = sources_by_domain(api_client, unique_user)["repeat.example"]

    post_feedback(api_client, unique_user, other, vote="down", reason="bad-source", scope="source", target="r")
    entries = sources_by_domain(api_client, unique_user)

    assert [d for d in entries if d.endswith("repeat.example")] == ["repeat.example"]
    assert entries["repeat.example"]["id"] == first["id"]
    assert entries["repeat.example"]["status"] == "caution"
    assert entries["repeat.example"]["note"] == first["note"], "a caution entry is not rewritten by another vote"


def test_bad_source_vote_never_softens_blocked(
    api_client: TestClient, unique_user: TestUser, recipe_factory: RecipeFactory, cleanup: None
):
    add_source(api_client, unique_user, "banned.example", "blocked", note="never")
    recipe = recipe_factory("https://banned.example/x")

    post_feedback(api_client, unique_user, recipe, vote="down", reason="bad-source", scope="source", target="b")

    entry = sources_by_domain(api_client, unique_user)["banned.example"]
    assert entry["status"] == "blocked"
    assert entry["note"] == "never"


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"vote": "down", "reason": "bad-source", "scope": "recipe"}, "recipe scope says nothing about the site"),
        (
            {"vote": "down", "reason": "too-spicy", "scope": "source", "target": "x"},
            "advisory: reason does not license source",
        ),
        ({"vote": "up", "scope": "source", "target": "x"}, "an up vote is not a demotion"),
    ],
)
def test_votes_that_do_not_license_a_source_claim_leave_the_list_alone(
    api_client: TestClient,
    unique_user: TestUser,
    recipe_factory: RecipeFactory,
    cleanup: None,
    payload: dict,
    why: str,
):
    domain = f"{random_string(8)}.example"
    recipe = recipe_factory(f"https://{domain}/x")

    post_feedback(api_client, unique_user, recipe, **payload)

    assert domain not in sources_by_domain(api_client, unique_user), why


def test_recipe_without_url_falls_back_to_a_host_shaped_target(
    api_client: TestClient, unique_user: TestUser, recipe_factory: RecipeFactory, cleanup: None
):
    recipe = recipe_factory(None)

    # a name is not a site: nothing to list
    post_feedback(
        api_client, unique_user, recipe, vote="down", reason="bad-source", scope="source", target="Allrecipes"
    )
    assert not any(d.startswith("allrecipes") for d in sources_by_domain(api_client, unique_user))

    # a host is
    post_feedback(
        api_client, unique_user, recipe, vote="down", reason="bad-source", scope="source", target="www.Allrecipes.com"
    )
    assert sources_by_domain(api_client, unique_user)["allrecipes.com"]["status"] == "caution"


def test_demotion_lands_in_the_voters_household_only(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, recipe_factory: RecipeFactory, cleanup: None
):
    recipe = recipe_factory("https://mine-only.example/x")

    post_feedback(api_client, unique_user, recipe, vote="down", reason="bad-source", scope="source", target="m")

    assert "mine-only.example" in sources_by_domain(api_client, unique_user)
    assert "mine-only.example" not in sources_by_domain(api_client, h2_user)


# ---------------------------------------------------------------------------
# bulk import


def test_bulk_import_refuses_blocked_url_and_imports_the_rest(
    api_client: TestClient, unique_user: TestUser, monkeypatch: MonkeyPatch, cleanup: None
):
    scraped: list[str] = []

    async def fake_create_from_html(url: str, repos, translator, html=None, on_progress=None, **_) -> tuple:
        scraped.append(url)
        slug = random_string(12)
        return Recipe(name=slug, slug=slug, org_url=url), None

    # the bulk scraper binds its own name for the scraper, so it is stubbed where it is used
    monkeypatch.setattr("mealie.services.scraper.recipe_bulk_scraper.create_from_html", fake_create_from_html)

    add_source(api_client, unique_user, "blocked-bulk.example", "blocked")
    blocked_url = "https://www.blocked-bulk.example/recipe/1"
    allowed_url = f"https://{random_string(10)}.example/recipe/2"

    response = api_client.post(
        api_routes.recipes_create_url_bulk,
        json={"imports": [{"url": blocked_url}, {"url": allowed_url}]},
        headers=unique_user.token,
    )
    assert response.status_code == 202, response.text
    report_id = response.json()["reportId"]

    try:
        assert scraped == [allowed_url], "only the allowed site reaches the scraper"

        response = api_client.get(api_routes.groups_reports_item_id(report_id), headers=unique_user.token)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["status"] == "partial"

        failures = [e for e in report["entries"] if not e["success"]]
        successes = [e for e in report["entries"] if e["success"]]
        assert len(failures) == 1 and len(successes) == 1
        assert "blocked-bulk.example" in failures[0]["message"]
        assert "blocked" in failures[0]["message"]
    finally:
        for recipe in api_client.get(api_routes.recipes, params={"perPage": -1}, headers=unique_user.token).json()[
            "items"
        ]:
            if recipe.get("orgUrl") == allowed_url:
                delete_recipe(api_client, unique_user, recipe["slug"])
