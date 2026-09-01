"""Integration tests for per-person scoped meal feedback (PLAN.md section 4, phase P1).

Structured as a sibling of `test_recipe_ratings.py`: recipes are built in a fixture, two-user
cases run on `user_tuple` (same group, same household), and every endpoint is addressed through
the generated `tests.utils.api_routes` module rather than a hand-built URL.

Two properties of this module are load-bearing and easy to lose in a refactor:

* Every recipe a test touches is created by `recipe_factory` and deleted when the test ends, and
  deleting a recipe takes its feedback and its `users_to_recipes` rows with it. That is what makes
  the exact-count assertions below safe despite `user_tuple`, `unique_user` and `h2_user` being
  module-scoped and therefore shared by every test in the file.
* The household reads are asserted from *both* members' tokens. Upstream 403s a cross-user rating
  read; if these endpoints were self-only, an assertion made only from the author's token would
  still pass.
"""

import contextlib
import time
from collections.abc import Callable, Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mealie.db.db_setup import session_context
from mealie.db.models.users.user_recipe_feedback import UserRecipeFeedback
from mealie.repos.all_repositories import AllRepositories
from mealie.schema.recipe.recipe import Recipe
from mealie.schema.user.user import UserRatingCreate, UserRatingUpdate
from mealie.schema.user.user_feedback import FEEDBACK_REASONS, FEEDBACK_SCOPES, LICENSED_SCOPES, UserFeedbackCreate
from tests.utils import api_routes
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser

RecipeFactory = Callable[[TestUser], Recipe]

DOWN_HAS_REASON_CONSTRAINT = "ck_user_recipe_feedback_down_has_reason"
"""The table's CHECK constraint, matched by name rather than by message text.

A foreign-key violation is an `IntegrityError` too, and its message also contains the word
"constraint" -- which is exactly how an earlier version of the sibling gate checker mistook one
for the other. The name is the only part of the message that says *which* rule fired.
"""


@pytest.fixture(scope="function")
def recipe_factory() -> Generator[RecipeFactory, None, None]:
    """Mints recipes on demand and deletes them when the test ends.

    A factory rather than a fixed list because the cross-household cases need recipes in
    `unique_user`'s group while the household-pair cases need them in `user_tuple`'s, and the
    same test sometimes needs two recipes to tell a filter apart from no filter.

    The teardown is what keeps the module's exact-count assertions honest: the users outlive the
    test, but their feedback does not, because `repository_recipes._delete_recipe` clears
    `user_recipe_feedback` and `users_to_recipes` for the recipe before removing it.
    """

    created: list[tuple[TestUser, Recipe]] = []

    def create(user: TestUser) -> Recipe:
        slug = random_string()
        recipe = user.repos.recipes.create(Recipe(user_id=user.user_id, group_id=user.group_id, name=slug, slug=slug))
        created.append((user, recipe))
        return recipe

    yield create

    for user, recipe in created:
        with contextlib.suppress(Exception):
            user.repos.recipes.delete(recipe.id, match_key="id")
            user.repos.session.commit()


def separate_events() -> None:
    """Force a provable gap between two events.

    `created_at` is stamped in Python at microsecond resolution and the household read orders by
    `(created_at, id)`. Two events written back to back would almost certainly differ anyway, but
    "almost certainly" is how an order assertion becomes a flaky one, and the `since` filter's
    boundary case needs the two rows to be genuinely separable.
    """

    time.sleep(0.02)


def post_feedback(api_client: TestClient, user: TestUser, recipe: Recipe, **payload) -> dict:
    """Cast one event and return the created row, failing loudly if the write was rejected."""

    response = api_client.post(
        api_routes.users_id_feedback_slug(user.user_id, recipe.slug), json=payload, headers=user.token
    )
    assert response.status_code == 201, response.text
    return response.json()


def own_feedback(api_client: TestClient, user: TestUser) -> list[dict]:
    """Everything this user has said, read through their own self-only route."""

    response = api_client.get(api_routes.users_id_feedback(user.user_id), headers=user.token)
    assert response.status_code == 200, response.text
    return response.json()["feedback"]


def household_feedback(api_client: TestClient, user: TestUser, **params) -> list[dict]:
    """Everything this user's household has said, read through the household route."""

    response = api_client.get(api_routes.households_feedback, params=params, headers=user.token)
    assert response.status_code == 200, response.text
    return response.json()["feedback"]


def household_ratings(api_client: TestClient, user: TestUser) -> list[dict]:
    """Every star held by this user's household."""

    response = api_client.get(api_routes.households_ratings, headers=user.token)
    assert response.status_code == 200, response.text
    return response.json()["ratings"]


def self_rating(api_client: TestClient, user: TestUser, recipe: Recipe):
    """This user's own star for a recipe, or None when they have not rated it."""

    response = api_client.get(api_routes.users_self_ratings_recipe_id(recipe.id), headers=user.token)
    if response.status_code == 404:
        return None

    assert response.status_code == 200, response.text
    return response.json()


def stored_advisory(event_id: str) -> bool:
    """Read the `advisory` column straight from the database.

    `UserFeedbackOut` recomputes `advisory` from the licensing map in a model validator, so the
    value in an API response proves the map was applied but not that the right value was ever
    written down. Downstream consumers query the column, so the column is what has to be right.
    A fresh session, because the API wrote through its own and a session that has already read
    inside an open transaction can be looking at an older snapshot.
    """

    with session_context() as session:
        stmt = select(UserRecipeFeedback).filter(UserRecipeFeedback.id == UUID(event_id))
        return session.execute(stmt).scalars().one().advisory


def insert_feedback_row(session: Session, user_id: UUID, recipe_id: UUID, vote: str, reason: str | None) -> None:
    """Write one row straight through the ORM model, with no schema in the way.

    `UserRecipeFeedback` carries no validation of its own -- `UserFeedbackIn` does that on the
    request -- so this reaches the database exactly as hand-written SQL or a partial restore
    would, while still binding GUIDs the way the engine expects. The `session=` keyword is
    required by `@auto_init()`.

    Raises whatever the commit raises; the caller decides which failures are the point.
    """

    session.add(
        UserRecipeFeedback(
            session=session,
            user_id=user_id,
            recipe_id=recipe_id,
            vote=vote,
            reason=reason,
            scope="recipe",
            target=None,
            note=None,
            advisory=False,
        )
    )
    session.commit()


def test_user_writes_and_reads_back_own_feedback(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    author, housemate = user_tuple
    recipe = recipe_factory(author)

    created = post_feedback(
        api_client,
        author,
        recipe,
        vote="down",
        reason="i-do-not-like-a-specific-ingredient",
        scope="ingredient",
        target="fennel",
        note="the fennel took over the whole pan",
    )

    assert created["userId"] == str(author.user_id)
    assert created["recipeId"] == str(recipe.id)
    assert created["vote"] == "down"
    assert created["reason"] == "i-do-not-like-a-specific-ingredient"
    assert created["scope"] == "ingredient"
    assert created["target"] == "fennel"
    assert created["note"] == "the fennel took over the whole pan"
    assert created["advisory"] is False  # ingredient is licensed by this reason
    assert created["id"]
    assert created["createdAt"]

    events = own_feedback(api_client, author)
    assert len(events) == 1
    assert events[0] == created

    # the self-only route is the author's own log, not the household's
    assert own_feedback(api_client, housemate) == []


def test_own_feedback_log_is_append_only(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """Saying the same thing twice records it twice (PLAN.md D1).

    The confidence rule these rows feed counts repeats, so a second identical vote has to become a
    second row. A table modelled as one mutable row per (user, recipe) -- which is exactly what
    `users_to_recipes` is -- would collapse these two into one and pass every other test here.
    """

    author = user_tuple[0]
    recipe = recipe_factory(author)

    first = post_feedback(api_client, author, recipe, vote="down", reason="too-much-work")
    separate_events()
    second = post_feedback(api_client, author, recipe, vote="down", reason="too-much-work")

    assert first["id"] != second["id"]

    events = own_feedback(api_client, author)
    assert len(events) == 2
    assert [event["id"] for event in events] == [first["id"], second["id"]]
    assert {event["reason"] for event in events} == {"too-much-work"}


def test_cross_user_feedback_write_is_forbidden(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """Reads widen to the household; writes do not widen at all (PLAN.md D4)."""

    author, housemate = user_tuple
    recipe = recipe_factory(author)

    response = api_client.post(
        api_routes.users_id_feedback_slug(housemate.user_id, recipe.slug),
        json={"vote": "down", "reason": "too-much-work"},
        headers=author.token,
    )
    assert response.status_code == 403

    # the 403 has to mean nothing was written, not merely that nothing was returned
    assert own_feedback(api_client, housemate) == []
    assert household_feedback(api_client, housemate) == []

    # reading somebody else's self-only log is refused the same way
    response = api_client.get(api_routes.users_id_feedback(housemate.user_id), headers=author.token)
    assert response.status_code == 403

    # ...and so is deleting under their id
    response = api_client.delete(
        api_routes.users_id_feedback_event_id(housemate.user_id, str(recipe.id)), headers=author.token
    )
    assert response.status_code == 403


def test_cross_user_delete_of_a_housemates_event_is_404(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """A housemate's event id, addressed under your own id, is not found rather than forbidden.

    403 would confirm the guessed id exists. Either way the row has to survive.
    """

    author, housemate = user_tuple
    recipe = recipe_factory(author)

    theirs = post_feedback(api_client, housemate, recipe, vote="up")

    response = api_client.delete(
        api_routes.users_id_feedback_event_id(author.user_id, theirs["id"]), headers=author.token
    )
    assert response.status_code == 404

    assert [event["id"] for event in own_feedback(api_client, housemate)] == [theirs["id"]]


def test_down_vote_with_missing_reason_is_422(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    author = user_tuple[0]
    recipe = recipe_factory(author)
    url = api_routes.users_id_feedback_slug(author.user_id, recipe.slug)

    response = api_client.post(url, json={"vote": "down"}, headers=author.token)
    assert response.status_code == 422

    response = api_client.post(url, json={"vote": "down", "reason": None}, headers=author.token)
    assert response.status_code == 422

    response = api_client.post(url, json={"vote": "down", "reason": ""}, headers=author.token)
    assert response.status_code == 422

    assert own_feedback(api_client, author) == []

    # the requirement is specific to `down`: a thumbs-up needs no explanation
    up = post_feedback(api_client, author, recipe, vote="up")
    assert up["reason"] is None


def test_unknown_reason_is_422(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    author = user_tuple[0]
    recipe = recipe_factory(author)
    url = api_routes.users_id_feedback_slug(author.user_id, recipe.slug)

    for reason in ("not-a-real-reason", "TOO-SPICY", "too spicy", "i-do-not-like-this-dish "):
        response = api_client.post(url, json={"vote": "down", "reason": reason}, headers=author.token)
        assert response.status_code == 422, f"{reason!r} should not be an accepted reason"

    # an unknown reason is rejected on any vote, not only on `down`
    response = api_client.post(url, json={"vote": "up", "reason": "not-a-real-reason"}, headers=author.token)
    assert response.status_code == 422

    assert own_feedback(api_client, author) == []


@pytest.mark.parametrize("reason", FEEDBACK_REASONS)
def test_every_reason_in_the_vocabulary_is_accepted(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory, reason: str
):
    """All twelve reasons of PLAN.md D2, and no more.

    The vocabulary is shared byte-for-byte with the planner skill, so a typo or a quietly dropped
    entry here is a defect the planner would only discover at the dinner table.
    """

    assert len(FEEDBACK_REASONS) == 12

    author = user_tuple[0]
    recipe = recipe_factory(author)

    created = post_feedback(api_client, author, recipe, vote="down", reason=reason)
    assert created["reason"] == reason
    assert created["scope"] == "recipe"
    assert created["advisory"] is False  # every reason licenses at least the recipe it was cast on


def test_unknown_vote_is_422(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    author = user_tuple[0]
    recipe = recipe_factory(author)
    url = api_routes.users_id_feedback_slug(author.user_id, recipe.slug)

    for vote in ("thumbs-down", "DOWN", "", "1"):
        response = api_client.post(url, json={"vote": vote}, headers=author.token)
        assert response.status_code == 422, f"{vote!r} should not be an accepted vote"

    response = api_client.post(url, json={}, headers=author.token)
    assert response.status_code == 422

    assert own_feedback(api_client, author) == []

    for vote in ("up", "down", "neutral"):
        payload = {"vote": vote, "reason": "too-heavy"} if vote == "down" else {"vote": vote}
        response = api_client.post(url, json=payload, headers=author.token)
        assert response.status_code == 201, f"{vote!r} should be an accepted vote"


def test_a_reason_sent_with_an_up_vote_is_kept(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """PLAN.md section 4 calls `reason` "required when vote = down, else null", but its own list
    of 422 conditions does not make a reason on an up vote an error. The schema takes the
    permissive reading -- `down` requires one, the others merely do not -- so a value the client
    deliberately sent is stored rather than silently dropped. This pins that resolution.
    """

    author = user_tuple[0]
    recipe = recipe_factory(author)

    created = post_feedback(api_client, author, recipe, vote="up", reason="i-do-not-like-this-dish")
    assert created["reason"] == "i-do-not-like-this-dish"
    assert [event["reason"] for event in own_feedback(api_client, author)] == ["i-do-not-like-this-dish"]


def test_a_scoped_claim_with_no_reason_is_advisory(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """Nothing licenses a claim that names no reason, so reaching past the recipe is advisory.

    Only reachable on `up` and `neutral`, since `down` demands a reason. The narrow claim on the
    same recipe, with the same missing reason, is not advisory -- which is the pair that tells
    "computed from the scope" apart from "always true when reason is null".
    """

    author = user_tuple[0]
    recipe = recipe_factory(author)

    scoped = post_feedback(api_client, author, recipe, vote="up", scope="dish", target="gumbo")
    assert scoped["reason"] is None
    assert scoped["advisory"] is True
    assert stored_advisory(scoped["id"]) is True

    unscoped = post_feedback(api_client, author, recipe, vote="neutral")
    assert unscoped["advisory"] is False
    assert stored_advisory(unscoped["id"]) is False


def test_wider_scope_than_the_reason_licenses_is_stored_as_advisory(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """Both directions, because a hard-coded `advisory = True` would pass either one alone.

    `too-spicy` licenses only the recipe, so claiming a whole cuisine on it is advisory.
    `too-repetitive` licenses cuisine outright, so the identical shape is not.
    """

    author = user_tuple[0]
    recipe = recipe_factory(author)

    over_reach = post_feedback(
        api_client, author, recipe, vote="down", reason="too-spicy", scope="cuisine", target="thai"
    )
    assert over_reach["advisory"] is True
    assert over_reach["scope"] == "cuisine"
    assert over_reach["target"] == "thai"
    assert stored_advisory(over_reach["id"]) is True

    licensed = post_feedback(
        api_client, author, recipe, vote="down", reason="too-repetitive", scope="cuisine", target="thai"
    )
    assert licensed["advisory"] is False
    assert licensed["scope"] == "cuisine"
    assert stored_advisory(licensed["id"]) is False

    # the narrow claim the same over-reaching reason does license
    narrow = post_feedback(api_client, author, recipe, vote="down", reason="too-spicy")
    assert narrow["advisory"] is False
    assert stored_advisory(narrow["id"]) is False

    # a wider scope is stored, never refused -- all three rows survived the round trip
    read_back = {event["id"]: event["advisory"] for event in own_feedback(api_client, author)}
    assert read_back == {over_reach["id"]: True, licensed["id"]: False, narrow["id"]: False}


@pytest.mark.parametrize("reason", FEEDBACK_REASONS)
def test_advisory_flag_matches_the_licensing_map(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory, reason: str
):
    """Every (reason, scope) pair in the vocabulary, checked against the map rather than a literal.

    Sixty combinations; the map says which are advisory and the server has to agree on all of them.
    """

    author = user_tuple[0]
    recipe = recipe_factory(author)

    for scope in FEEDBACK_SCOPES:
        expected = scope not in LICENSED_SCOPES[reason]
        created = post_feedback(
            api_client,
            author,
            recipe,
            vote="down",
            reason=reason,
            scope=scope,
            target=None if scope == "recipe" else random_string(),
        )
        assert created["advisory"] is expected, f"{reason} at {scope} scope"
        assert stored_advisory(created["id"]) is expected, f"{reason} at {scope} scope, as stored"


def test_non_recipe_scope_with_missing_target_is_422(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    author = user_tuple[0]
    recipe = recipe_factory(author)
    url = api_routes.users_id_feedback_slug(author.user_id, recipe.slug)

    for scope in (scope for scope in FEEDBACK_SCOPES if scope != "recipe"):
        for target in (None, "", "   "):
            response = api_client.post(
                url,
                json={"vote": "down", "reason": "i-do-not-like-this-dish", "scope": scope, "target": target},
                headers=author.token,
            )
            assert response.status_code == 422, f"scope {scope!r} with target {target!r} should be refused"

        response = api_client.post(
            url,
            json={"vote": "down", "reason": "i-do-not-like-this-dish", "scope": scope},
            headers=author.token,
        )
        assert response.status_code == 422, f"scope {scope!r} with no target at all should be refused"

    assert own_feedback(api_client, author) == []

    # the default scope needs no target, and a target that is only whitespace is no target
    created = post_feedback(api_client, author, recipe, vote="down", reason="i-do-not-like-this-dish")
    assert created["scope"] == "recipe"
    assert created["target"] is None

    created = post_feedback(
        api_client, author, recipe, vote="down", reason="i-do-not-like-this-dish", scope="dish", target="  gumbo  "
    )
    assert created["target"] == "gumbo"


def test_unknown_scope_is_422(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    author = user_tuple[0]
    recipe = recipe_factory(author)
    url = api_routes.users_id_feedback_slug(author.user_id, recipe.slug)

    for scope in ("household", "RECIPE", "everything"):
        response = api_client.post(
            url,
            json={"vote": "down", "reason": "too-repetitive", "scope": scope, "target": "thai"},
            headers=author.token,
        )
        assert response.status_code == 422, f"{scope!r} should not be an accepted scope"

    assert own_feedback(api_client, author) == []


def test_feedback_on_an_unknown_recipe_is_404(api_client: TestClient, user_tuple: tuple[TestUser, TestUser]):
    author = user_tuple[0]
    response = api_client.post(
        api_routes.users_id_feedback_slug(author.user_id, random_string()),
        json={"vote": "down", "reason": "too-heavy"},
        headers=author.token,
    )
    assert response.status_code == 404


def test_household_feedback_returns_both_members_events(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """The read that retires the planner's two-token dance (PLAN.md D4).

    Asserted from both tokens: upstream's rating read 403s a cross-user call, so a household read
    that had inherited that restriction would still satisfy an assertion made only by the author.
    """

    first, second = user_tuple
    recipe = recipe_factory(first)

    first_event = post_feedback(api_client, first, recipe, vote="down", reason="too-much-work", note="three pans")
    separate_events()
    second_event = post_feedback(api_client, second, recipe, vote="up")

    for reader in user_tuple:
        rows = household_feedback(api_client, reader, recipeId=str(recipe.id))
        assert [row["id"] for row in rows] == [first_event["id"], second_event["id"]], "oldest first"

        by_user = {row["userId"]: row for row in rows}
        assert by_user[str(first.user_id)]["username"] == first.username
        assert by_user[str(first.user_id)]["vote"] == "down"
        assert by_user[str(first.user_id)]["reason"] == "too-much-work"
        assert by_user[str(first.user_id)]["note"] == "three pans"
        assert by_user[str(second.user_id)]["username"] == second.username
        assert by_user[str(second.user_id)]["vote"] == "up"
        assert by_user[str(second.user_id)]["reason"] is None

        # the same two events are there without a filter, and nothing from outside the household is
        member_ids = {str(member.user_id) for member in user_tuple}
        unfiltered = household_feedback(api_client, reader)
        assert {first_event["id"], second_event["id"]} <= {row["id"] for row in unfiltered}
        assert {row["userId"] for row in unfiltered} <= member_ids


def test_household_feedback_filters_narrow_the_result(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    first, second = user_tuple
    kept = recipe_factory(first)
    other = recipe_factory(first)

    down = post_feedback(api_client, first, kept, vote="down", reason="took-too-long")
    separate_events()
    up = post_feedback(api_client, second, kept, vote="up")
    elsewhere = post_feedback(api_client, first, other, vote="neutral")

    reader = second

    by_recipe = household_feedback(api_client, reader, recipeId=str(kept.id))
    assert [row["id"] for row in by_recipe] == [down["id"], up["id"]]
    assert elsewhere["id"] not in {row["id"] for row in by_recipe}

    by_other_recipe = household_feedback(api_client, reader, recipeId=str(other.id))
    assert [row["id"] for row in by_other_recipe] == [elsewhere["id"]]

    assert [row["id"] for row in household_feedback(api_client, reader, recipeId=str(kept.id), vote="down")] == [
        down["id"]
    ]
    assert [row["id"] for row in household_feedback(api_client, reader, recipeId=str(kept.id), vote="up")] == [up["id"]]
    assert household_feedback(api_client, reader, recipeId=str(kept.id), vote="neutral") == []

    # `since` is inclusive, and `separate_events` guarantees the earlier row falls outside it
    from_second = household_feedback(api_client, reader, recipeId=str(kept.id), since=up["createdAt"])
    assert [row["id"] for row in from_second] == [up["id"]]

    assert [
        row["id"] for row in household_feedback(api_client, reader, recipeId=str(kept.id), since=down["createdAt"])
    ] == [
        down["id"],
        up["id"],
    ]
    assert household_feedback(api_client, reader, recipeId=str(kept.id), since="2999-01-01T00:00:00+00:00") == []

    # a recipe nobody in the household has voted on is an empty answer, not an unfiltered one
    assert household_feedback(api_client, reader, recipeId=str(recipe_factory(first).id)) == []


def test_household_feedback_unknown_vote_is_422(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """An unknown vote must say so rather than answer 200 with an empty list.

    An empty 200 reads as "nobody voted that way", which is a different claim from "that is not
    a vote" and would quietly mislead the planner.
    """

    reader = user_tuple[0]
    recipe = recipe_factory(reader)
    post_feedback(api_client, reader, recipe, vote="down", reason="too-heavy")

    for vote in ("thumbs-down", "DOWN", ""):
        response = api_client.get(api_routes.households_feedback, params={"vote": vote}, headers=reader.token)
        assert response.status_code == 422, f"{vote!r} should not be an accepted vote filter"

    # the known votes still work, so the 422 above is about the value and not the parameter
    assert len(household_feedback(api_client, reader, vote="down")) == 1


def test_other_household_sees_only_its_own_feedback(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, recipe_factory: RecipeFactory
):
    """`h2_user` shares a group with `unique_user` but not a household.

    The household is the trust boundary, so the answer is "only your own household's rows", which
    is not the same claim as "nothing". Both halves are asserted: the neighbour's row is absent
    *and* the reader's own row is present, so an endpoint that always returned an empty list
    would fail here.
    """

    recipe = recipe_factory(unique_user)

    mine = post_feedback(api_client, unique_user, recipe, vote="down", reason="not-flavorful-enough")
    theirs = post_feedback(api_client, h2_user, recipe, vote="up")

    neighbour_rows = household_feedback(api_client, h2_user, recipeId=str(recipe.id))
    assert [row["id"] for row in neighbour_rows] == [theirs["id"]]
    assert neighbour_rows[0]["userId"] == str(h2_user.user_id)
    assert neighbour_rows[0]["username"] == h2_user.username

    my_rows = household_feedback(api_client, unique_user, recipeId=str(recipe.id))
    assert [row["id"] for row in my_rows] == [mine["id"]]
    assert my_rows[0]["userId"] == str(unique_user.user_id)

    # neither household's read leaks the other's row, in either direction
    assert theirs["id"] not in {row["id"] for row in household_feedback(api_client, unique_user)}
    assert mine["id"] not in {row["id"] for row in household_feedback(api_client, h2_user)}


def test_other_household_ratings_are_not_visible_across_households(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser, recipe_factory: RecipeFactory
):
    recipe = recipe_factory(unique_user)

    post_feedback(api_client, unique_user, recipe, vote="down", reason="too-heavy")
    post_feedback(api_client, h2_user, recipe, vote="up")

    mine = [row for row in household_ratings(api_client, unique_user) if row["recipeId"] == str(recipe.id)]
    assert [(row["userId"], row["rating"]) for row in mine] == [(str(unique_user.user_id), 1)]

    theirs = [row for row in household_ratings(api_client, h2_user) if row["recipeId"] == str(recipe.id)]
    assert [(row["userId"], row["rating"]) for row in theirs] == [(str(h2_user.user_id), 5)]


def test_another_group_sees_none_of_the_households_feedback(
    api_client: TestClient,
    user_tuple: tuple[TestUser, TestUser],
    unique_user_fn_scoped: TestUser,
    recipe_factory: RecipeFactory,
):
    """A user in a different group entirely, whose own household really is empty.

    `unique_user_fn_scoped` registers a brand new group and household, so the first read below is
    exactly `[]` rather than merely "none of theirs". The second half proves that empty answer was
    the boundary working and not the endpoint being broken: the same caller, on the same route,
    sees their own event the moment they cast one.
    """

    outsider = unique_user_fn_scoped
    first, second = user_tuple
    recipe = recipe_factory(first)

    theirs = [
        post_feedback(api_client, first, recipe, vote="down", reason="did-not-work-for-our-household"),
        post_feedback(api_client, second, recipe, vote="up"),
    ]

    assert household_feedback(api_client, outsider) == []
    assert household_ratings(api_client, outsider) == []

    own_recipe = recipe_factory(outsider)
    mine = post_feedback(api_client, outsider, own_recipe, vote="down", reason="too-spicy")

    rows = household_feedback(api_client, outsider)
    assert [row["id"] for row in rows] == [mine["id"]]
    assert {row["id"] for row in rows}.isdisjoint({event["id"] for event in theirs})

    # and the recipe id of another group's recipe buys no reach into it
    assert household_feedback(api_client, outsider, recipeId=str(recipe.id)) == []

    # the household pair still sees its own two rows, so nothing above emptied the wrong table
    assert {event["id"] for event in theirs} <= {row["id"] for row in household_feedback(api_client, first)}


def test_delete_recipe_deletes_its_feedback(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """Recipe deletion has to take the feedback with it, or it starts failing on stale rows."""

    first, second = user_tuple
    doomed = recipe_factory(first)
    survivor = recipe_factory(first)

    doomed_events = [
        post_feedback(api_client, first, doomed, vote="down", reason="bad-source", scope="source", target="some-blog"),
        post_feedback(api_client, second, doomed, vote="up"),
    ]
    survivor_event = post_feedback(api_client, first, survivor, vote="down", reason="too-heavy")

    assert len(household_feedback(api_client, first, recipeId=str(doomed.id))) == 2

    first.repos.recipes.delete(doomed.id, match_key="id")
    first.repos.session.commit()

    response = api_client.get(api_routes.recipes_slug(doomed.slug), headers=first.token)
    assert response.status_code == 404

    remaining = {row["id"] for row in household_feedback(api_client, first)}
    assert remaining.isdisjoint({event["id"] for event in doomed_events})
    assert survivor_event["id"] in remaining, "deleting one recipe must not clear the rest"

    assert [event["id"] for event in own_feedback(api_client, first)] == [survivor_event["id"]]
    assert own_feedback(api_client, second) == []

    with session_context() as session:
        stmt = select(UserRecipeFeedback).filter(UserRecipeFeedback.recipe_id == doomed.id)
        assert session.execute(stmt).scalars().all() == []


def test_delete_own_feedback_event(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    author = user_tuple[0]
    recipe = recipe_factory(author)

    kept = post_feedback(api_client, author, recipe, vote="down", reason="too-much-work")
    separate_events()
    doomed = post_feedback(api_client, author, recipe, vote="down", reason="took-too-long")

    response = api_client.delete(
        api_routes.users_id_feedback_event_id(author.user_id, doomed["id"]), headers=author.token
    )
    assert response.status_code == 200
    assert response.json()["id"] == doomed["id"]

    assert [event["id"] for event in own_feedback(api_client, author)] == [kept["id"]]
    assert [row["id"] for row in household_feedback(api_client, author, recipeId=str(recipe.id))] == [kept["id"]]

    # deleting it again is a 404, not a second success
    response = api_client.delete(
        api_routes.users_id_feedback_event_id(author.user_id, doomed["id"]), headers=author.token
    )
    assert response.status_code == 404


def test_votes_set_the_caster_rating_only(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """PLAN.md D3: down projects onto a 1-star, up onto a 5-star, for the caster and nobody else."""

    caster, housemate = user_tuple
    disliked = recipe_factory(caster)
    liked = recipe_factory(caster)

    assert self_rating(api_client, caster, disliked) is None

    post_feedback(api_client, caster, disliked, vote="down", reason="too-heavy")
    assert self_rating(api_client, caster, disliked)["rating"] == 1

    post_feedback(api_client, caster, liked, vote="up")
    assert self_rating(api_client, caster, liked)["rating"] == 5

    # the housemate said nothing, so the housemate has no star on either recipe
    assert self_rating(api_client, housemate, disliked) is None
    assert self_rating(api_client, housemate, liked) is None

    # and a later up vote moves the caster's own star rather than adding a second one
    post_feedback(api_client, caster, disliked, vote="up")
    assert self_rating(api_client, caster, disliked)["rating"] == 5

    ratings = [row for row in household_ratings(api_client, caster) if row["recipeId"] == str(disliked.id)]
    assert len(ratings) == 1


def test_neutral_vote_leaves_the_caster_rating_untouched(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """`neutral` records an opinion too mild to overwrite a star the person chose by hand."""

    caster = user_tuple[0]
    rated = recipe_factory(caster)
    unrated = recipe_factory(caster)

    response = api_client.post(
        api_routes.users_id_ratings_slug(caster.user_id, rated.slug),
        json=UserRatingUpdate(rating=3).model_dump(),
        headers=caster.token,
    )
    assert response.status_code == 200

    event = post_feedback(api_client, caster, rated, vote="neutral", note="fine, I guess")
    assert event["vote"] == "neutral"
    assert self_rating(api_client, caster, rated)["rating"] == 3

    # nor does it invent a star where there was none
    post_feedback(api_client, caster, unrated, vote="neutral")
    assert self_rating(api_client, caster, unrated) is None


def test_vote_preserves_the_caster_rating_favorite_flag(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """A vote says nothing about favoriting, so the flag beside the star has to survive it."""

    caster = user_tuple[0]
    recipe = recipe_factory(caster)

    response = api_client.post(api_routes.users_id_favorites_slug(caster.user_id, recipe.slug), headers=caster.token)
    assert response.status_code == 200
    assert self_rating(api_client, caster, recipe)["isFavorite"] is True

    post_feedback(api_client, caster, recipe, vote="down", reason="not-flavorful-enough")

    rating = self_rating(api_client, caster, recipe)
    assert rating["rating"] == 1
    assert rating["isFavorite"] is True


def test_household_ratings_returns_both_members_stars(
    api_client: TestClient, user_tuple: tuple[TestUser, TestUser], recipe_factory: RecipeFactory
):
    """PLAN.md D4: one token, one call, both people's stars.

    Upstream's `/api/users/{id}/ratings` 403s a cross-user read, which is exactly what this
    replaces -- so the assertion that matters is that each member can see the *other* member's row.
    """

    first, second = user_tuple
    recipe = recipe_factory(first)

    post_feedback(api_client, first, recipe, vote="down", reason="too-much-work")
    post_feedback(api_client, second, recipe, vote="up")

    for reader in user_tuple:
        rows = [row for row in household_ratings(api_client, reader) if row["recipeId"] == str(recipe.id)]
        by_user = {row["userId"]: row for row in rows}

        assert set(by_user) == {str(first.user_id), str(second.user_id)}
        assert by_user[str(first.user_id)]["rating"] == 1
        assert by_user[str(first.user_id)]["username"] == first.username
        assert by_user[str(second.user_id)]["rating"] == 5
        assert by_user[str(second.user_id)]["username"] == second.username

    # the self-only route upstream provides is still self-only, which is why the above is needed
    response = api_client.get(api_routes.users_id_ratings(second.user_id), headers=first.token)
    assert response.status_code == 403


def test_repository_group_filter_confines_feedback_to_its_own_group(
    unique_user: TestUser,
    unique_user_fn_scoped: TestUser,
    unfiltered_database: AllRepositories,
    recipe_factory: RecipeFactory,
):
    """The group filter inside `RepositoryUserFeedback.get_by_users`, on its own.

    The cross-group boundary is defended twice: `HouseholdFeedbackController.members` only ever
    asks about the caller's own household, and the repository filters by group underneath it.
    Removing either layer alone leaves `test_another_group_sees_none_of_the_households_feedback`
    green, so that test cannot catch a single-point regression in either. This one drives the
    repository directly, with the controller untouched, so the filter has to hold by itself.

    The situation the filter exists for is one user id owning rows in two groups -- what a user
    moved between groups leaves behind. No API path can produce it, which is why the rows here
    are written through an unscoped repository rather than through the endpoints.
    """

    insider, outsider = unique_user, unique_user_fn_scoped
    assert insider.group_id != outsider.group_id, "these fixtures must really be in two different groups"

    here = recipe_factory(insider)
    there = recipe_factory(outsider)

    mine_here = unfiltered_database.user_feedback.create(
        UserFeedbackCreate(user_id=insider.user_id, recipe_id=here.id, vote="down", reason="too-much-work")
    )
    mine_there = unfiltered_database.user_feedback.create(
        UserFeedbackCreate(user_id=insider.user_id, recipe_id=there.id, vote="down", reason="too-spicy")
    )
    assert mine_here.id != mine_there.id

    # positive control: an unscoped repository sees both rows, so the narrower answer below is the
    # filter doing its job rather than nothing having been written
    unscoped = {row.id for row in unfiltered_database.user_feedback.get_by_users([insider.user_id])}
    assert {mine_here.id, mine_there.id} <= unscoped

    scoped = {row.id for row in insider.repos.user_feedback.get_by_users([insider.user_id])}
    assert mine_here.id in scoped, "a group-scoped read must still return its own group's rows"
    assert mine_there.id not in scoped, "a group-scoped read must not reach into another group"

    # the same user id, read from the other group's side, yields that group's row and only it
    assert {row.id for row in outsider.repos.user_feedback.get_by_users([insider.user_id])} == {mine_there.id}

    # the self-only read carries the same filter, so it cannot become the way around this one
    own = {row.id for row in insider.repos.user_feedback.get_by_user(insider.user_id)}
    assert mine_here.id in own
    assert mine_there.id not in own


def test_repository_group_filter_confines_ratings_to_its_own_group(
    unique_user: TestUser,
    unique_user_fn_scoped: TestUser,
    unfiltered_database: AllRepositories,
    recipe_factory: RecipeFactory,
):
    """The same tripwire for `RepositoryUserRatings.get_by_users` (PLAN.md D4).

    Ratings are where the leak was actually found: upstream's `get_by_user` carries no group
    filter, so a user moved between groups kept star rows pointing at their old group's recipes
    and the household read surfaced them. The fix was a separate, group-filtered `get_by_users`,
    and this asserts the filter is on it -- independently of the household member list, which
    would hide the regression if it were the only thing under test.
    """

    insider, outsider = unique_user, unique_user_fn_scoped
    assert insider.group_id != outsider.group_id, "these fixtures must really be in two different groups"

    here = recipe_factory(insider)
    there = recipe_factory(outsider)

    mine_here = unfiltered_database.user_ratings.create(
        UserRatingCreate(user_id=insider.user_id, recipe_id=here.id, rating=4)
    )
    mine_there = unfiltered_database.user_ratings.create(
        UserRatingCreate(user_id=insider.user_id, recipe_id=there.id, rating=2)
    )
    assert mine_here.id != mine_there.id

    unscoped = {row.id for row in unfiltered_database.user_ratings.get_by_users([insider.user_id])}
    assert {mine_here.id, mine_there.id} <= unscoped

    scoped = {row.id for row in insider.repos.user_ratings.get_by_users([insider.user_id])}
    assert mine_here.id in scoped, "a group-scoped read must still return its own group's rows"
    assert mine_there.id not in scoped, "a group-scoped read must not reach into another group"

    assert {row.id for row in outsider.repos.user_ratings.get_by_users([insider.user_id])} == {mine_there.id}

    # upstream's singular `get_by_user` is deliberately left unfiltered: it backs the self-only
    # `/api/users/{id}/ratings`, where the caller owns every row it can return. Pinned so that the
    # difference between the two methods stays a decision rather than an oversight -- and it is a
    # second positive control, since it returns both rows from the group-scoped repository.
    singular = {row.id for row in insider.repos.user_ratings.get_by_user(insider.user_id)}
    assert {mine_here.id, mine_there.id} <= singular


def test_constraint_down_needs_reason_is_enforced_by_the_database(unique_user: TestUser, recipe_factory: RecipeFactory):
    """A `down` row with a NULL reason is refused by the table, not merely by pydantic.

    `UserFeedbackOut` inherits `UserFeedbackIn`'s validators, so such a row would raise while
    being *read* and fail the whole household feed rather than one row. Nothing in the API can
    write one -- which is exactly why only a database-level test can cover the constraint that
    makes it unreachable by hand SQL or a partial restore too.

    Every rejection is paired with insertions that must succeed. Without them a table that
    refused every write would satisfy the `pytest.raises` below and prove nothing.
    """

    recipe = recipe_factory(unique_user)

    # a session of its own: a failed commit poisons the transaction it happened in, and the
    # module-scoped `session` fixture is shared with every user's repositories
    with session_context() as session:
        with pytest.raises(IntegrityError) as caught:
            insert_feedback_row(session, unique_user.user_id, recipe.id, vote="down", reason=None)

        # matched by name, because a foreign-key violation is an IntegrityError too and its
        # message also contains the word "constraint"
        assert DOWN_HAS_REASON_CONSTRAINT in str(caught.value.orig)

        # the rejected insert left the transaction unusable; without this every write below would
        # fail for the wrong reason and the positive controls would look like the constraint
        session.rollback()

        insert_feedback_row(session, unique_user.user_id, recipe.id, vote="down", reason="too-spicy")
        insert_feedback_row(session, unique_user.user_id, recipe.id, vote="up", reason=None)
        insert_feedback_row(session, unique_user.user_id, recipe.id, vote="neutral", reason=None)

    with session_context() as session:
        stmt = select(UserRecipeFeedback).filter(UserRecipeFeedback.recipe_id == recipe.id)
        rows = session.execute(stmt).scalars().all()

    # the three accepted shapes are all there, and the refused one is not: the constraint is
    # exactly "a down vote carries a reason", and a reason stays optional for every other vote
    assert len(rows) == 3
    assert {(row.vote, row.reason) for row in rows} == {
        ("down", "too-spicy"),
        ("up", None),
        ("neutral", None),
    }
