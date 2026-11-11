"""Integration tests for pantry staples.

A staple is a food the household usually has. Five properties carry the feature and each has a
test here: a staple stays off a shopping list filled from a recipe; marking one covers the plain
forms of it, including one a later import creates, and never a form that might be a different
thing to buy; a list can say what it left off; an ingredient somebody explicitly asks for is added
even when it is a staple; and one household's pantry never changes another's.

Food names are unique per group, so every name here carries a random stem.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from mealie.schema.recipe.recipe import Recipe
from mealie.schema.recipe.recipe_ingredient import IngredientFood, RecipeIngredient, SaveIngredientFood
from mealie.schema.recipe.recipe_settings import RecipeSettings
from tests.utils import api_routes
from tests.utils.assertion_helpers import assert_deserialize
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser


def make_food(api_client: TestClient, user: TestUser, name: str) -> dict:
    response = api_client.post(api_routes.foods, json={"name": name}, headers=user.token)
    return assert_deserialize(response, 201)


def stem() -> str:
    """A made-up food name no other test uses: one word, letters only."""

    return "".join(ch for ch in random_string(24).lower() if ch.isalpha()) or "zzz"


def pantry(api_client: TestClient, user: TestUser) -> dict:
    return assert_deserialize(api_client.get(api_routes.households_pantry, headers=user.token), 200)


def staple_ids(api_client: TestClient, user: TestUser) -> set[str]:
    return {food["id"] for food in pantry(api_client, user)["staples"]}


def mark(api_client: TestClient, user: TestUser, food_id: str, expected_status: int = 200) -> dict:
    response = api_client.put(api_routes.households_pantry_food_id(food_id), headers=user.token)
    assert response.status_code == expected_status, response.text
    return response.json()


def unmark(api_client: TestClient, user: TestUser, food_id: str) -> int:
    return api_client.delete(api_routes.households_pantry_food_id(food_id), headers=user.token).status_code


def make_recipe(user: TestUser, foods: list[dict]) -> Recipe:
    ingredients = [
        RecipeIngredient(food_id=food["id"], food=IngredientFood.model_validate(food), quantity=1) for food in foods
    ]
    return user.repos.recipes.create(
        Recipe(
            user_id=user.user_id,
            group_id=user.group_id,
            name=random_string(),
            recipe_ingredient=ingredients,
            settings=RecipeSettings(),
        )
    )


def make_list(api_client: TestClient, user: TestUser) -> str:
    response = api_client.post(api_routes.households_shopping_lists, json={"name": random_string()}, headers=user.token)
    return assert_deserialize(response, 201)["id"]


def add_recipe(api_client: TestClient, user: TestUser, list_id: str, recipe: Recipe, **params) -> dict:
    response = api_client.post(
        api_routes.households_shopping_lists_item_id_recipe(list_id),
        json=[{"recipeId": str(recipe.id), **params}],
        headers=user.token,
    )
    return assert_deserialize(response, 200)


def foods_on(shopping_list: dict) -> set[str]:
    return {item["foodId"] for item in shopping_list["listItems"] if item.get("foodId")}


def left_off(api_client: TestClient, user: TestUser, list_id: str) -> list[dict]:
    response = api_client.get(api_routes.households_pantry_shopping_lists_list_id(list_id), headers=user.token)
    return assert_deserialize(response, 200)["items"]


def test_a_staple_stays_off_a_list_filled_from_a_recipe(api_client: TestClient, unique_user: TestUser):
    salt = make_food(api_client, unique_user, stem())
    lentils = make_food(api_client, unique_user, stem())
    recipe = make_recipe(unique_user, [salt, lentils])

    mark(api_client, unique_user, salt["id"])

    list_id = make_list(api_client, unique_user)
    assert foods_on(add_recipe(api_client, unique_user, list_id, recipe)) == {lentils["id"]}


def test_unmarking_puts_the_food_back_on_the_next_list(api_client: TestClient, unique_user: TestUser):
    salt = make_food(api_client, unique_user, stem())
    recipe = make_recipe(unique_user, [salt])

    mark(api_client, unique_user, salt["id"])
    assert unmark(api_client, unique_user, salt["id"]) == 200
    assert salt["id"] not in staple_ids(api_client, unique_user)

    list_id = make_list(api_client, unique_user)
    assert foods_on(add_recipe(api_client, unique_user, list_id, recipe)) == {salt["id"]}


def test_marking_twice_changes_nothing(api_client: TestClient, unique_user: TestUser):
    salt = make_food(api_client, unique_user, stem())

    mark(api_client, unique_user, salt["id"])
    again = mark(api_client, unique_user, salt["id"])

    assert again["alsoMarked"] == []
    assert [food["id"] for food in pantry(api_client, unique_user)["staples"]].count(salt["id"]) == 1


def test_marking_a_staple_covers_its_plain_forms_only(api_client: TestClient, unique_user: TestUser):
    base = stem()
    salt = make_food(api_client, unique_user, base)
    kosher = make_food(api_client, unique_user, f"kosher {base}")
    fine_sea = make_food(api_client, unique_user, f"Fine sea {base}")
    garlic = make_food(api_client, unique_user, f"garlic {base}")
    spray = make_food(api_client, unique_user, f"{base} spray")

    marked = mark(api_client, unique_user, salt["id"])

    assert marked["food"]["id"] == salt["id"]
    assert {food["id"] for food in marked["alsoMarked"]} == {kosher["id"], fine_sea["id"]}

    staples = staple_ids(api_client, unique_user)
    assert {salt["id"], kosher["id"], fine_sea["id"]} <= staples
    # "garlic salt" may be a different thing to buy, and "salt spray" is not a salt at all
    assert garlic["id"] not in staples
    assert spray["id"] not in staples

    # the qualified form is offered, naming the staple it is a form of; the non-form is not
    suggestions = {s["id"]: s for s in pantry(api_client, unique_user)["suggestions"]}
    assert suggestions[garlic["id"]]["because"] == base
    assert spray["id"] not in suggestions


def test_a_food_created_later_inherits_from_a_staple(api_client: TestClient, unique_user: TestUser):
    base = stem()
    oil = make_food(api_client, unique_user, base)
    mark(api_client, unique_user, oil["id"])

    household = unique_user.repos.households.get_by_slug_or_id(unique_user.household_id)
    assert household

    # what a recipe import does when it meets a spelling it has not seen
    plain = make_food(api_client, unique_user, f"extra-virgin {base}")
    assert plain["householdsWithIngredientFood"] == [household.slug]
    assert plain["id"] in staple_ids(api_client, unique_user)

    qualified = make_food(api_client, unique_user, f"toasted {base}")
    assert qualified["householdsWithIngredientFood"] == []
    assert qualified["id"] not in staple_ids(api_client, unique_user)

    # and the inherited one really stays off a list
    recipe = make_recipe(unique_user, [plain, qualified])
    list_id = make_list(api_client, unique_user)
    assert foods_on(add_recipe(api_client, unique_user, list_id, recipe)) == {qualified["id"]}


def test_an_unmarked_form_stays_unmarked(api_client: TestClient, unique_user: TestUser):
    base = stem()
    salt = make_food(api_client, unique_user, base)
    kosher = make_food(api_client, unique_user, f"kosher {base}")
    mark(api_client, unique_user, salt["id"])

    assert unmark(api_client, unique_user, kosher["id"]) == 200
    assert kosher["id"] not in staple_ids(api_client, unique_user)

    # creating some other food must not quietly mark it again
    make_food(api_client, unique_user, f"flaky {base}")
    staples = staple_ids(api_client, unique_user)
    assert kosher["id"] not in staples
    assert salt["id"] in staples


def test_a_list_reports_what_it_left_off(api_client: TestClient, unique_user: TestUser):
    salt = make_food(api_client, unique_user, stem())
    sugar = make_food(api_client, unique_user, stem())
    lentils = make_food(api_client, unique_user, stem())
    flour = make_food(api_client, unique_user, stem())
    tacos = make_recipe(unique_user, [salt, lentils])
    # each recipe keeps something to buy: upstream drops a recipe from a list once no item on the
    # list refers to it, so a recipe made only of staples stops being one of the list's recipes
    cake = make_recipe(unique_user, [salt, sugar, flour])

    mark(api_client, unique_user, salt["id"])
    mark(api_client, unique_user, sugar["id"])

    list_id = make_list(api_client, unique_user)
    assert left_off(api_client, unique_user, list_id) == []

    add_recipe(api_client, unique_user, list_id, tacos)
    add_recipe(api_client, unique_user, list_id, cake)

    items = {item["food"]["id"]: item for item in left_off(api_client, unique_user, list_id)}
    assert set(items) == {salt["id"], sugar["id"]}
    assert {recipe["id"] for recipe in items[salt["id"]]["recipes"]} == {str(tacos.id), str(cake.id)}
    assert {recipe["id"] for recipe in items[sugar["id"]]["recipes"]} == {str(cake.id)}
    assert not any(item["onList"] for item in items.values())

    # running out: the food goes on the list by hand, and the report says it is there
    response = api_client.post(
        api_routes.households_shopping_items,
        json={"shoppingListId": list_id, "foodId": salt["id"], "quantity": 0},
        headers=unique_user.token,
    )
    assert response.status_code == 201, response.text

    items = {item["food"]["id"]: item for item in left_off(api_client, unique_user, list_id)}
    assert items[salt["id"]]["onList"] is True
    assert items[sugar["id"]]["onList"] is False


def test_an_ingredient_asked_for_by_name_is_added_even_when_it_is_a_staple(
    api_client: TestClient, unique_user: TestUser
):
    salt = make_food(api_client, unique_user, stem())
    lentils = make_food(api_client, unique_user, stem())
    recipe = make_recipe(unique_user, [salt, lentils])
    mark(api_client, unique_user, salt["id"])

    # the add-to-list dialog sends the ingredients the person ticked; ticking a staple there is
    # how somebody says they have run out, so it must not be dropped on the way in
    chosen = [
        {"quantity": 1, "foodId": salt["id"], "food": {"id": salt["id"], "name": salt["name"]}},
    ]
    list_id = make_list(api_client, unique_user)
    shopping_list = add_recipe(api_client, unique_user, list_id, recipe, recipeIngredients=chosen)

    assert foods_on(shopping_list) == {salt["id"]}


def test_an_unknown_food_or_list_is_a_404(api_client: TestClient, unique_user: TestUser):
    missing = "00000000-0000-4000-8000-000000000000"

    mark(api_client, unique_user, missing, expected_status=404)
    assert unmark(api_client, unique_user, missing) == 404

    response = api_client.get(api_routes.households_pantry_shopping_lists_list_id(missing), headers=unique_user.token)
    assert response.status_code == 404


def test_one_households_pantry_is_not_anothers(api_client: TestClient, unique_user: TestUser, h2_user: TestUser):
    # same group, different household: the food is shared, the flag is not
    base = stem()
    salt = make_food(api_client, unique_user, base)
    recipe = make_recipe(unique_user, [salt])

    mark(api_client, unique_user, salt["id"])
    assert salt["id"] not in staple_ids(api_client, h2_user)

    h2_list = make_list(api_client, h2_user)
    assert foods_on(add_recipe(api_client, h2_user, h2_list, recipe)) == {salt["id"]}
    assert left_off(api_client, h2_user, h2_list) == []

    # and h2 marking it does not disturb the first household's flag, in either direction
    mark(api_client, h2_user, salt["id"])
    assert unmark(api_client, h2_user, salt["id"]) == 200
    assert salt["id"] in staple_ids(api_client, unique_user)

    # a new plain form is marked for the household that has the staple, and only for it
    household = unique_user.repos.households.get_by_slug_or_id(unique_user.household_id)
    assert household
    mark(api_client, h2_user, make_food(api_client, unique_user, stem())["id"])
    kosher = make_food(api_client, unique_user, f"kosher {base}")
    assert kosher["householdsWithIngredientFood"] == [household.slug]

    # one household cannot read another's list through the pantry
    own_list = make_list(api_client, unique_user)
    response = api_client.get(api_routes.households_pantry_shopping_lists_list_id(own_list), headers=h2_user.token)
    assert response.status_code == 404


def test_another_groups_food_cannot_be_marked(api_client: TestClient, unique_user: TestUser, g2_user: TestUser):
    theirs = g2_user.repos.ingredient_foods.create(
        SaveIngredientFood(id=uuid4(), name=stem(), group_id=g2_user.group_id)
    )

    mark(api_client, unique_user, str(theirs.id), expected_status=404)
    assert str(theirs.id) not in staple_ids(api_client, unique_user)
    assert str(theirs.id) not in staple_ids(api_client, g2_user)
