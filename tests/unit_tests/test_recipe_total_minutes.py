"""The parsed cook time: what it reads, and that saving a recipe keeps it true.

`recipes.total_minutes` is derived from `total_time`, which is free text. Two
things have to hold or the browse filter quietly lies: the parser has to read
every form the household's recipes actually use, and the stored number has to
follow the text through every write path — including one where a client sends
back the number it was given while editing the time.
"""

import pytest
from sqlalchemy.orm import Session

from mealie.pkgs.cooktime import total_minutes
from mealie.schema.recipe.recipe import Recipe
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The forms in the household's own library, read off the live instance.
        ("30 minutes", 30),
        ("22 minutes", 22),
        ("17 minutes", 17),
        ("1 Hour 15 Minutes", 75),
        # What schema.org recipes carry, which is what the scraper stores.
        ("PT50M", 50),
        ("PT1H30M", 90),
        ("PT0S", 0),
        ("P1DT2H", 1560),
        # Abbreviations and mixed case, which people type and sites print.
        ("45 min", 45),
        ("1 hr 5 min", 65),
        ("2 HOURS", 120),
        ("1 hour 30 mins", 90),
        # A bare number means minutes; it is the other thing Mealie writes.
        ("40", 40),
        (40, 40),
        (40.4, 40),
        # Seconds round into the minute rather than disappearing.
        ("90 seconds", 2),
    ],
)
def test_reads_every_cook_time_form(raw, expected):
    assert total_minutes(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "some evening",
        "a while",
        "P",
        "PT",
        True,  # bool is an int subclass, and True is not one minute
        -30,
    ],
)
def test_refuses_to_guess(raw):
    """A cook time nobody can read is None, never a number.

    This is the half that keeps the filter honest: a weeknight query that
    silently swept in every unreadable recipe as "30 minutes" would be worse
    than one that leaves them out and says so.
    """
    assert total_minutes(raw) is None


def test_a_length_it_cannot_read_is_not_zero():
    """Guards the specific wrong fix: falling back to 0 instead of None.

    Zero would pass every "under 45 minutes" filter ever written.
    """
    assert total_minutes("some evening") is not None or total_minutes("some evening") is None
    assert total_minutes("some evening") != 0
    # and the control, so this is not vacuously true of everything
    assert total_minutes("PT0S") == 0


def test_hook_populates_total_minutes_on_save(unique_user: TestUser):
    """Saving a recipe stores the parsed length, through the repository the API uses."""
    database = unique_user.repos
    recipe = database.recipes.create(
        Recipe(
            user_id=unique_user.user_id,
            group_id=unique_user.group_id,
            name=random_string(),
            total_time="1 Hour 15 Minutes",
        )
    )
    assert recipe.total_minutes == 75


def test_hook_follows_an_edited_cook_time(unique_user: TestUser):
    """Editing the time moves the number with it, rather than leaving the old one."""
    database = unique_user.repos
    recipe = database.recipes.create(
        Recipe(
            user_id=unique_user.user_id,
            group_id=unique_user.group_id,
            name=random_string(),
            total_time="1 Hour 15 Minutes",
        )
    )
    assert recipe.total_minutes == 75

    recipe.total_time = "25 minutes"
    updated = database.recipes.update(recipe.slug, recipe)
    assert updated.total_minutes == 25

    # and it clears when the time is removed, rather than keeping a stale number
    updated.total_time = None
    cleared = database.recipes.update(updated.slug, updated)
    assert cleared.total_minutes is None


def test_hook_ignores_a_total_minutes_sent_by_a_client(unique_user: TestUser):
    """The derived value wins over anything a client sends.

    `totalMinutes` is on the recipe schema so the UI can read it, which means the
    recipe editor PUTs it back. When somebody has just changed the cook time, the
    number it sends is the one from before the edit. If input won, the badge and
    the filter would both go on describing the old recipe.
    """
    database = unique_user.repos
    recipe = database.recipes.create(
        Recipe(
            user_id=unique_user.user_id,
            group_id=unique_user.group_id,
            name=random_string(),
            total_time="25 minutes",
        )
    )

    recipe.total_time = "1 Hour 15 Minutes"
    recipe.total_minutes = 25  # what a client that read the recipe before the edit sends
    updated = database.recipes.update(recipe.slug, recipe)
    assert updated.total_minutes == 75, "the stored length must come from total_time, not from the request"


def test_hook_survives_a_reload_from_the_database(unique_user: TestUser, session: Session):
    """The number is on the row, not just on the in-memory object.

    Without this the earlier assertions would still pass on an object that was
    never actually written, and every filter would read NULL.
    """
    database = unique_user.repos
    created = database.recipes.create(
        Recipe(
            user_id=unique_user.user_id,
            group_id=unique_user.group_id,
            name=random_string(),
            total_time="PT1H30M",
        )
    )
    session.expire_all()
    reloaded = database.recipes.get_one(created.slug)
    assert reloaded is not None
    assert reloaded.total_minutes == 90
