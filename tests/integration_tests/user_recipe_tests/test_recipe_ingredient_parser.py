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
