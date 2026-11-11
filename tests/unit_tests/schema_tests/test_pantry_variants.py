"""The naming rule behind pantry staples, held to the food names it was written against.

Every name below is one the household's own library held on 2026-11-10. The rule decides whether a
food is left off a shopping list without anybody having said so, so the cases that matter most are
the ones where it must say no.
"""

import pytest

from mealie.schema.household.pantry import NEUTRAL_QUALIFIERS, PLAIN, QUALIFIED, SAME, name_words, variant_of


@pytest.mark.parametrize(
    ("name", "staple"),
    [
        ("salt", "salt"),
        ("Salt", "salt"),
        ("extra-virgin olive oil", "extra virgin olive oil"),
    ],
)
def test_the_same_name_is_the_same_food(name: str, staple: str):
    assert variant_of(name, staple) == SAME


@pytest.mark.parametrize(
    ("name", "staple"),
    [
        ("kosher salt", "salt"),
        ("sea salt", "salt"),
        ("Fine sea salt", "salt"),
        ("extra-virgin olive oil", "olive oil"),
        ("freshly black pepper", "black pepper"),
        ("cold water", "water"),
        ("filtered water", "water"),
        ("granulated sugar", "sugar"),
    ],
)
def test_a_plain_form_is_the_same_thing_to_buy(name: str, staple: str):
    assert variant_of(name, staple) == PLAIN


@pytest.mark.parametrize(
    ("name", "staple"),
    [
        ("brown sugar", "sugar"),
        ("light brown sugar", "sugar"),
        ("garlic salt", "salt"),
        ("Diamond Crystal kosher salt", "salt"),
        ("sparkling water", "water"),
        ("reserved pasta water", "water"),
        ("extra virgin Spanish olive oil", "olive oil"),
        ("salted butter", "butter"),
        ("toasted sesame oil", "sesame oil"),
    ],
)
def test_any_other_word_in_front_might_be_a_different_thing_to_buy(name: str, staple: str):
    assert variant_of(name, staple) == QUALIFIED


@pytest.mark.parametrize(
    ("name", "staple"),
    [
        ("olive oil spray", "olive oil"),
        ("red bell pepper", "black pepper"),
        ("red pepper flakes", "pepper"),
        ("oil", "olive oil"),
        ("peanut butter", "salt"),
        ("butter beans", "butter"),
        ("", "salt"),
        ("salt", ""),
        (None, "salt"),
    ],
)
def test_a_food_that_does_not_end_with_the_staple_is_not_a_form_of_it(name: str | None, staple: str):
    assert variant_of(name, staple) is None


def test_names_are_compared_as_words():
    assert name_words("Extra-Virgin  Olive Oil!") == ("extra", "virgin", "olive", "oil")
    # "saltine" is not salt, and a staple is never matched inside a longer word
    assert variant_of("saltine crackers", "salt") is None
    assert variant_of("unsalt", "salt") is None


def test_no_qualifier_names_a_flavour_colour_or_source():
    # the words most likely to be added by mistake; each turns a staple into a different product
    assert not NEUTRAL_QUALIFIERS & {"brown", "white", "light", "dark", "fresh", "smoked", "garlic", "toasted"}
