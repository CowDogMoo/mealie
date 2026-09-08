import asyncio
import re
from dataclasses import dataclass

import pytest
from pydantic import UUID4
from rapidfuzz import fuzz
from text_unidecode import unidecode

from mealie.db.db_setup import session_context
from mealie.lang.providers import get_locale_provider
from mealie.repos.repository_factory import AllRepositories
from mealie.schema.recipe.recipe_ingredient import IngredientUnit, SaveIngredientUnit
from mealie.services.parser_services import RegisteredParser, get_parser
from mealie.services.parser_services.ingredient_parser import nlp_parser_accepts_unit


@dataclass
class TestIngredient:
    input: str
    quantity: float
    unit: str
    food: str
    comments: str


def normalize(val: str) -> str:
    val = unidecode(val).lower().strip()
    val = re.sub(r"[^a-z0-9\s]", "", val)
    return val


@pytest.mark.parametrize(
    "test_ingredient",
    [
        TestIngredient("½ cup all-purpose flour", 0.5, "cup", "all-purpose flour", ""),
        TestIngredient("1 ½ teaspoons ground black pepper", 1.5, "teaspoon", "black pepper", "ground"),
        TestIngredient("⅔ cup unsweetened flaked coconut", 0.667, "cup", "unsweetened flaked coconut", ""),
        TestIngredient("⅓ cup panko bread crumbs", 0.333, "cup", "panko bread crumbs", ""),
        TestIngredient("1/8 cup all-purpose flour", 0.125, "cup", "all-purpose flour", ""),
        TestIngredient("1/32 cup all-purpose flour", 0.031, "cup", "all-purpose flour", ""),
        TestIngredient("1 1/2 cups chopped onion ", 1.5, "cup", "onion", "chopped"),
        TestIngredient(
            "2 pounds russet potatoes, peeled, and cut into 3/4-inch cubes  ",
            2,
            "pound",
            "russet potatoes",
            "peeled, and cut into 3/4 inch cubes",
        ),
        TestIngredient("2 teaspoons salt (to taste) ", 2, "teaspoon", "salt", "to taste"),
        TestIngredient("1/2 cup", 0.5, "cup", "", ""),
    ],
)
def test_nlp_parser(unique_local_group_id: UUID4, test_ingredient: TestIngredient):
    with session_context() as session:
        parser = get_parser(RegisteredParser.nlp, unique_local_group_id, session, get_locale_provider())
        parsed = asyncio.run(parser.parse_one(test_ingredient.input))
        ing = parsed.ingredient

        assert ing.quantity == pytest.approx(test_ingredient.quantity)
        if ing.unit:
            assert ing.unit.name == test_ingredient.unit
        else:
            assert not test_ingredient.unit
        if ing.food:
            assert ing.food.name == test_ingredient.food
        else:
            assert not test_ingredient.food
        if ing.note:
            assert ing.note == test_ingredient.comments
        else:
            assert not test_ingredient.comments


@pytest.mark.parametrize(
    ("source_str", "expected_str"),
    [
        (
            "2 teaspoon chopped fresh or dried rosemary",
            "2 teaspoon fresh rosemary or dried rosemary chopped",
        ),
        (
            "153 grams 00 flour (1 cup plus 1 tablespoon)",
            "153 gram 00 flour or 1 cup and 1 tablespoon",
        ),
        (
            "153 grams all-purpose flour (1 cup plus 1 tablespoon and 2 teaspoons)",
            "153 gram all-purpose flour or 1 cup plus 1 tablespoon and 2 teaspoons",
        ),
        (
            "2 cups chicken broth or beef broth",
            "2 cup chicken broth or beef broth",
        ),
        (
            "2 tablespoons (30ml) vegetable oil",
            "2 tablespoon vegetable oil or 30 milliliter",
        ),
        (
            "1 cup fresh basil or 2 tablespoons dried basil",
            "1 cup fresh basil or 2 tablespoons dried basil",
        ),
    ],
)
@pytest.mark.asyncio
async def test_nlp_parser_keeps_all_text(unique_local_group_id: UUID4, source_str: str, expected_str: str):
    with session_context() as session:
        parser = get_parser(RegisteredParser.nlp, unique_local_group_id, session, get_locale_provider())
        parsed = await parser.parse_one(source_str)

    ing = parsed.ingredient

    # The parser behavior may change slightly, so we check that it's pretty close rather than exact
    # fuzz.ratio returns a string from 0 - 100 where 100 is an exact match
    score = fuzz.ratio(ing.display, expected_str)
    assert score >= 90, f"'{ing.display}' does not sufficiently match expected '{expected_str}'"


@pytest.mark.parametrize(
    ("plural", "singular"),
    [
        ("cup ** 2", "cup ** 2"),
        ("cups)", "cup)"),
        ("[cups", "[cup"),
        ("cups", "cup\\"),
        ("fl\\oz", "fluid ounce"),
    ],
)
def test_nlp_parser_rejects_units_the_library_cannot_compile(plural: str, singular: str):
    assert nlp_parser_accepts_unit(plural, singular) is False


@pytest.mark.parametrize(
    ("plural", "singular"),
    [
        ("cups", "cup"),
        ("fl. oz.", "fl. oz."),
        ("My Very Long Unit Names", "My Very Long Unit Name"),
        ("glugs", "glug"),
    ],
)
def test_nlp_parser_accepts_ordinary_units(plural: str, singular: str):
    assert nlp_parser_accepts_unit(plural, singular) is True


@pytest.mark.asyncio
async def test_nlp_parser_survives_unit_with_regex_metacharacters(unique_db: AllRepositories, unique_local_group_id):
    unique_db.ingredient_units.create_many(
        [
            SaveIngredientUnit(name="cup ** 2", group_id=unique_local_group_id),
            SaveIngredientUnit(name="tbsp\\", plural_name="tbsps\\", group_id=unique_local_group_id),
            SaveIngredientUnit(name="glug", plural_name="glugs", group_id=unique_local_group_id),
        ]
    )

    parser = get_parser(RegisteredParser.nlp, unique_local_group_id, unique_db.session, get_locale_provider())

    parsed = await parser.parse_one("2 cups all-purpose flour")
    assert parsed.ingredient.quantity == pytest.approx(2)
    assert parsed.ingredient.unit and parsed.ingredient.unit.name == "cup"
    assert parsed.ingredient.food and parsed.ingredient.food.name == "all-purpose flour"

    # the well-formed custom unit next to the broken ones is still handed to the library:
    # without it the library returns no unit at all for "glugs"
    parsed = await parser.parse_one("2 glugs olive oil")
    assert parsed.ingredient.unit and parsed.ingredient.unit.name == "glug"


@pytest.mark.parametrize(
    ("line", "wrong_unit"),
    [
        ("1 tablespoon olive oil", "teaspoon"),
        ("2 tablespoons butter", "teaspoon"),
        ("500 milliliters water", "liter"),
        ("4 fluid ounces cream", "ounce"),
    ],
)
@pytest.mark.asyncio
async def test_nlp_parser_never_fuzzy_matches_one_measurement_to_another(
    unique_db: AllRepositories, unique_local_group_id, line: str, wrong_unit: str
):
    # the household knows only the near-miss unit, the way a small custom unit list does
    unique_db.ingredient_units.create_many(
        [
            SaveIngredientUnit(name="teaspoon", group_id=unique_local_group_id),
            SaveIngredientUnit(name="liter", group_id=unique_local_group_id),
            SaveIngredientUnit(name="ounce", group_id=unique_local_group_id),
        ]
    )
    parser = get_parser(RegisteredParser.nlp, unique_local_group_id, unique_db.session, get_locale_provider())

    parsed = await parser.parse_one(line)
    unit = parsed.ingredient.unit
    assert unit is not None
    assert unit.name.lower() != wrong_unit, f"{line!r} was matched to the household's {wrong_unit!r}"
    assert not isinstance(unit, IngredientUnit), "an unknown measurement must come back unmatched, not guessed"


@pytest.mark.asyncio
async def test_nlp_parser_still_fuzzy_matches_the_same_measurement_and_unmeasured_units(
    unique_db: AllRepositories, unique_local_group_id
):
    unique_db.ingredient_units.create_many(
        [
            # no plural set: "cups" must still land on it through the fuzzy match
            SaveIngredientUnit(name="Cup", group_id=unique_local_group_id),
            SaveIngredientUnit(name="Stalk", group_id=unique_local_group_id),
            SaveIngredientUnit(name="tablespoon", plural_name="tablespoons", group_id=unique_local_group_id),
        ]
    )
    parser = get_parser(RegisteredParser.nlp, unique_local_group_id, unique_db.session, get_locale_provider())

    cups = await parser.parse_one("2 cups flour")
    assert isinstance(cups.ingredient.unit, IngredientUnit) and cups.ingredient.unit.name == "Cup"

    stalks = await parser.parse_one("2 stalks celery")
    assert isinstance(stalks.ingredient.unit, IngredientUnit) and stalks.ingredient.unit.name == "Stalk"

    tbsp = await parser.parse_one("1 tbsp olive oil")
    assert isinstance(tbsp.ingredient.unit, IngredientUnit) and tbsp.ingredient.unit.name == "tablespoon"
