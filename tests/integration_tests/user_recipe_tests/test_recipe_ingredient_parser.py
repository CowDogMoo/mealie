from fastapi.testclient import TestClient

from mealie.schema.recipe.recipe_ingredient import CreateIngredientUnit
from tests.utils import api_routes
from tests.utils.fixture_schemas import TestUser


def test_parse_ingredients_with_unit_named_like_a_broken_regex(api_client: TestClient, unique_user: TestUser):
    """
    A unit whose name is not a valid regular expression (the live instance once
    held "cup ** 2") used to make every parse request in the group fail with a
    500. The parser must skip that unit and keep parsing.
    """
    unit = CreateIngredientUnit(name="cup ** 2").model_dump(by_alias=True)
    response = api_client.post(api_routes.units, json=unit, headers=unique_user.token)
    assert response.status_code == 201
    unit_id = response.json()["id"]

    try:
        response = api_client.post(
            api_routes.parser_ingredients,
            json={"parser": "nlp", "ingredients": ["2 cups all-purpose flour", "1 tablespoon olive oil"]},
            headers=unique_user.token,
        )
        assert response.status_code == 200

        flour, oil = response.json()
        assert flour["ingredient"]["quantity"] == 2
        assert flour["ingredient"]["unit"]["name"] == "cup"
        assert flour["ingredient"]["food"]["name"] == "all-purpose flour"
        assert oil["ingredient"]["quantity"] == 1
        assert oil["ingredient"]["unit"]["name"] == "tablespoon"

        response = api_client.post(
            api_routes.parser_ingredient,
            json={"parser": "nlp", "ingredient": "3 cloves garlic, minced"},
            headers=unique_user.token,
        )
        assert response.status_code == 200
        assert response.json()["ingredient"]["food"]["name"] == "garlic"
    finally:
        api_client.delete(api_routes.units_item_id(unit_id), headers=unique_user.token)


def test_parse_ingredients_does_not_turn_tablespoons_into_teaspoons(api_client: TestClient, unique_user: TestUser):
    """
    With no "tablespoon" unit in the household, string similarity used to hand
    every tablespoon line the household's "teaspoon" unit (ratio 77.8 clears the
    unit threshold of 70), tripling nothing and dividing every quantity by three
    on the shopping list. A measurement the household lacks must come back
    unmatched instead.
    """
    units = api_client.get(api_routes.units, params={"perPage": -1}, headers=unique_user.token).json()["items"]
    for unit in units:
        names = {unit["name"].lower(), (unit["abbreviation"] or "").lower()}
        if names & {"tablespoon", "tablespoons", "tbsp", "tbsps"}:
            assert api_client.delete(api_routes.units_item_id(unit["id"]), headers=unique_user.token).status_code == 200
    if not any(unit["name"].lower() == "teaspoon" for unit in units):
        created = api_client.post(api_routes.units, json={"name": "teaspoon"}, headers=unique_user.token)
        assert created.status_code == 201

    response = api_client.post(
        api_routes.parser_ingredients,
        json={"parser": "nlp", "ingredients": ["1 tablespoon olive oil", "2 tbsp soy sauce"]},
        headers=unique_user.token,
    )
    assert response.status_code == 200
    for parsed in response.json():
        unit = parsed["ingredient"]["unit"]
        assert unit is not None
        assert unit["name"].lower() != "teaspoon", parsed["input"]
        assert unit.get("id") is None, f"{parsed['input']!r} was matched to an existing unit: {unit}"
