"""The household's pantry staples, and what they keep off a shopping list.

A staple is a food with upstream's per-household "on hand" flag. Upstream already leaves a flagged
food off when a recipe is added to a shopping list; what was missing is everything around that:

- one list to manage, rather than a checkbox inside each food's edit dialog;
- cover for the other spellings of the same product, so marking salt also covers "fine sea salt",
  including the ones a recipe import has not created yet (`inherit`);
- an account of what a list left off, so "we usually have it" never silently becomes "we forgot
  it" -- the list can show the staples its recipes use and put one back with a tap.

The naming rule is in :mod:`mealie.schema.household.pantry`.
"""

from pydantic import UUID4

from mealie.repos.all_repositories import get_repositories
from mealie.repos.repository_factory import AllRepositories
from mealie.schema.household.pantry import (
    MAX_SUGGESTIONS,
    PLAIN,
    QUALIFIED,
    SAME,
    STARTER_STAPLES,
    PantryFood,
    PantryLeftOff,
    PantryLeftOffItem,
    PantryLeftOffRecipe,
    PantryMarkOut,
    PantryOut,
    PantrySuggestion,
    variant_of,
)
from mealie.schema.recipe.recipe import Recipe
from mealie.schema.recipe.recipe_ingredient import IngredientFood
from mealie.services._base_service import BaseService


class PantryError(Exception):
    """Base for the refusals this service raises. The controller maps each to a status code."""


class PantryFoodNotFoundError(PantryError):
    """No food with that id belongs to this group."""


class PantryListNotFoundError(PantryError):
    """No shopping list with that id belongs to this household."""


class PantryService(BaseService):
    """`repos` must be household-scoped: the household whose pantry this is comes from it."""

    def __init__(self, repos: AllRepositories) -> None:
        super().__init__()
        self.repos = repos
        self.pantry = repos.pantry

    # ------------------------------------------------------------------ reads

    def get(self) -> PantryOut:
        foods = self.pantry.foods()
        marked = self.pantry.staple_ids()
        staples = [food for food in foods if food.id in marked]
        return PantryOut(staples=staples, suggestions=self._suggestions(foods, staples))

    def _suggestions(self, foods: list[PantryFood], staples: list[PantryFood]) -> list[PantrySuggestion]:
        """Foods the household has not marked but probably has.

        Forms of a staple it already marked come first, each naming that staple; then foods that
        match the starter list. A qualified form of a *starter* ("brown sugar") is not offered
        until the household has marked the staple itself, so an empty pantry is offered a short
        list of basics rather than every food with "salt" in its name.
        """

        marked = {food.id for food in staples}
        from_staples: list[PantrySuggestion] = []
        from_starters: list[tuple[int, PantrySuggestion]] = []

        for food in foods:
            if food.id in marked:
                continue

            because = next(
                (staple.name for staple in staples if variant_of(food.name, staple.name) in (SAME, PLAIN, QUALIFIED)),
                None,
            )
            if because is not None:
                from_staples.append(PantrySuggestion(**food.model_dump(), because=because))
                continue

            rank = next(
                (i for i, starter in enumerate(STARTER_STAPLES) if variant_of(food.name, starter) in (SAME, PLAIN)),
                None,
            )
            if rank is not None:
                from_starters.append((rank, PantrySuggestion(**food.model_dump())))

        from_starters.sort(key=lambda pair: (pair[0], pair[1].name.lower()))
        return (from_staples + [suggestion for _, suggestion in from_starters])[:MAX_SUGGESTIONS]

    def left_off(self, shopping_list_id: UUID4) -> PantryLeftOff:
        """The staples the recipes on a shopping list call for, which the list therefore left off.

        Read from the list's own recipe references, so it describes the list as it stands: remove
        a recipe and its staples stop being reported. Each item says whether somebody has put the
        food on the list anyway, which is how a household that has run out says so.

        One recipe is not reported: one made of nothing but staples. It puts no item on the list,
        and upstream drops a recipe's reference at the next write once no item refers to it.
        """

        shopping_list = self.repos.group_shopping_lists.get_one(shopping_list_id)
        if shopping_list is None:
            raise PantryListNotFoundError("shopping list not found")

        household = self.repos.households.get_by_slug_or_id(shopping_list.household_id)
        if household is None:
            return PantryLeftOff()

        # recipes are read group-wide, the same way the shopping list service reads them when it
        # adds one: a household may shop for a recipe that another household in the group owns
        recipes = get_repositories(self.repos.session, group_id=self.repos.group_id, household_id=None).recipes
        on_list = {item.food_id for item in shopping_list.list_items if item.food_id}

        items: dict[UUID4, PantryLeftOffItem] = {}
        for reference in shopping_list.recipe_references:
            recipe = recipes.get_one(reference.recipe_id, "id")
            if recipe is None:
                continue

            for food in self._staples_in(recipe, household.slug, seen=set()):
                item = items.setdefault(
                    food.id,
                    PantryLeftOffItem(
                        food=PantryFood(id=food.id, name=food.name, plural_name=food.plural_name),
                        on_list=food.id in on_list,
                    ),
                )
                if all(listed.id != recipe.id for listed in item.recipes):
                    item.recipes.append(PantryLeftOffRecipe(id=recipe.id, slug=recipe.slug, name=recipe.name))

        return PantryLeftOff(items=sorted(items.values(), key=lambda item: item.food.name.lower()))

    def _staples_in(self, recipe: Recipe, household_slug: str, seen: set[UUID4]) -> list[IngredientFood]:
        """The on-hand foods a recipe uses, through any recipes it uses as ingredients."""

        if recipe.id is None or recipe.id in seen:
            return []
        seen.add(recipe.id)

        foods: list[IngredientFood] = []
        for ingredient in recipe.recipe_ingredient:
            if isinstance(ingredient.referenced_recipe, Recipe):
                foods.extend(self._staples_in(ingredient.referenced_recipe, household_slug, seen))
            elif isinstance(ingredient.food, IngredientFood):
                if household_slug in ingredient.food.households_with_ingredient_food:
                    foods.append(ingredient.food)
        return foods

    # ----------------------------------------------------------------- writes

    def mark(self, food_id: UUID4) -> PantryMarkOut:
        """Mark a food as a staple, along with every plain form of it the group already has."""

        food = self.pantry.food(food_id)
        if food is None:
            raise PantryFoodNotFoundError("food not found")

        already = self.pantry.staple_ids()
        also = [
            other
            for other in self.pantry.foods()
            if other.id != food.id and other.id not in already and variant_of(other.name, food.name) in (SAME, PLAIN)
        ]
        self.pantry.mark([food.id, *(other.id for other in also)])
        return PantryMarkOut(food=food, also_marked=also)

    def unmark(self, food_id: UUID4) -> None:
        """Take one food off the staples. Its other forms stay: each is listed and removable."""

        if self.pantry.food(food_id) is None:
            raise PantryFoodNotFoundError("food not found")
        self.pantry.unmark(food_id)

    def inherit(self, food: IngredientFood) -> IngredientFood:
        """Mark a newly created food for every household that already has a plain form of it.

        Recipe imports create foods all the time, and each new spelling of a staple would otherwise
        reach the shopping list once before anybody marked it. Only a plain form inherits -- the
        staple's name with nothing but neutral qualifiers in front -- and it is marked like any
        other staple, so it shows in the pantry and can be taken off for good.
        """

        inherited = False
        for household_id, staples in self.pantry.staples_by_household().items():
            if any(variant_of(food.name, staple) in (SAME, PLAIN) for staple in staples):
                self.pantry.mark([food.id], household_id=household_id)
                inherited = True

        if not inherited:
            return food
        return self.repos.ingredient_foods.get_one(food.id) or food
