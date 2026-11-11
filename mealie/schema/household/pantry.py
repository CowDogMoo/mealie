"""Pantry staples: the foods a household usually has, and so does not want on a shopping list.

There is no new storage here. A staple is a food carrying upstream's per-household "on hand" flag
(`households_to_ingredient_foods`), which the shopping list service already honours when a recipe
is added to a list. What this module adds is the vocabulary for managing that flag as one list
instead of one food at a time, and the rule for which other foods a staple speaks for.

**This file is the copy of record for that rule.** A food is the *same product* as a staple when
its name is the staple's name with only words from `NEUTRAL_QUALIFIERS` in front: "fine sea salt"
is salt, "extra-virgin olive oil" is olive oil. Those are marked on hand along with the staple, now
and whenever a recipe import creates one later, because the alternative is that every new spelling
of salt lands on the list once. Any other word in front ("brown sugar", "garlic salt", "sparkling
water") might be a different thing to buy, so it is only ever *suggested*: leaving an ingredient
off a list that the household does not have costs a dinner, and putting one on that it does have
costs a tick.
"""

import re

from pydantic import UUID4, ConfigDict

from mealie.schema._mealie import MealieModel

NEUTRAL_QUALIFIERS: frozenset[str] = frozenset(
    {
        # salt
        "kosher",
        "sea",
        "table",
        "iodized",
        "flaky",
        # grind and texture
        "fine",
        "coarse",
        "coarsely",
        "ground",
        "cracked",
        "freshly",
        "granulated",
        "superfine",
        "caster",
        # purity
        "pure",
        "plain",
        "regular",
        "extra",
        "virgin",
        "organic",
        "filtered",
        "tap",
        # temperature
        "cold",
        "warm",
        "hot",
        "boiling",
        "lukewarm",
        "ice",
    }
)
"""Words that describe the form of a product without making it a different product."""

STARTER_STAPLES: tuple[str, ...] = (
    "salt",
    "black pepper",
    "sugar",
    "olive oil",
    "water",
    "vegetable oil",
    "cooking spray",
    "flour",
    "all purpose flour",
    "baking soda",
    "baking powder",
    "cornstarch",
    "butter",
    "honey",
    "soy sauce",
    "garlic powder",
    "onion powder",
    "cumin",
    "paprika",
    "chili powder",
    "oregano",
    "cinnamon",
    "red pepper flakes",
    "vanilla extract",
)
"""What most kitchens keep. Only ever used to *suggest* foods the household has not marked yet."""

MAX_SUGGESTIONS = 24

SAME = "same"
PLAIN = "plain"
QUALIFIED = "qualified"

_NOT_A_WORD = re.compile(r"[^a-z0-9]+")


def name_words(name: str | None) -> tuple[str, ...]:
    """A food name as lower-case words, with punctuation and hyphens treated as spaces."""

    return tuple(word for word in _NOT_A_WORD.split((name or "").lower()) if word)


def variant_of(name: str | None, staple: str | None) -> str | None:
    """How the food called `name` relates to the staple called `staple`.

    `SAME` when the names match, `PLAIN` when `name` is the staple with only neutral qualifiers in
    front (the same thing to buy), `QUALIFIED` when it is the staple with any other word in front
    (possibly a different thing to buy), and None when it is not a form of the staple at all. The
    staple has to be the *end* of the name: "olive oil spray" is a spray, not an olive oil.
    """

    words, staple_words = name_words(name), name_words(staple)
    if not words or not staple_words or len(words) < len(staple_words):
        return None
    if words[-len(staple_words) :] != staple_words:
        return None

    qualifiers = words[: -len(staple_words)]
    if not qualifiers:
        return SAME
    return PLAIN if all(word in NEUTRAL_QUALIFIERS for word in qualifiers) else QUALIFIED


class PantryFood(MealieModel):
    id: UUID4
    name: str
    plural_name: str | None = None
    model_config = ConfigDict(from_attributes=True)


class PantrySuggestion(PantryFood):
    because: str | None = None
    """The staple this food is a form of, or None when it is only on the starter list."""


class PantryOut(MealieModel):
    staples: list[PantryFood] = []
    suggestions: list[PantrySuggestion] = []


class PantryMarkOut(MealieModel):
    food: PantryFood
    also_marked: list[PantryFood] = []
    """Plain forms of `food` that were marked along with it."""


class PantryLeftOffRecipe(MealieModel):
    id: UUID4
    slug: str | None = None
    name: str | None = None


class PantryLeftOffItem(MealieModel):
    food: PantryFood
    recipes: list[PantryLeftOffRecipe] = []
    on_list: bool = False
    """True when somebody has put this food on the list anyway."""


class PantryLeftOff(MealieModel):
    items: list[PantryLeftOffItem] = []
