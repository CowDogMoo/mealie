from collections.abc import Iterable

from pydantic import UUID4
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mealie.db.models.recipe.ingredient import IngredientFoodModel, households_to_ingredient_foods
from mealie.schema.household.pantry import PantryFood


class RepositoryPantry:
    """Reads and writes upstream's per-household "on hand" flag as one list.

    The flag is a row in `households_to_ingredient_foods`. Upstream only writes it by replacing a
    food's whole list of households through `PUT /api/foods/{id}`; this writes one household's row
    and nothing else, so marking a staple can never drop another household's flag.

    Every statement is filtered by `group_id`, and the household-scoped methods by `household_id`,
    so a food id from another group reads as missing rather than as somebody else's food.
    """

    def __init__(self, session: Session, *, group_id: UUID4 | None, household_id: UUID4 | None) -> None:
        self.session = session
        self.group_id = group_id
        self.household_id = household_id

    def foods(self) -> list[PantryFood]:
        """Every food in the group, by name."""

        stmt = (
            select(IngredientFoodModel.id, IngredientFoodModel.name, IngredientFoodModel.plural_name)
            .where(IngredientFoodModel.group_id == self.group_id)
            .order_by(IngredientFoodModel.name, IngredientFoodModel.id)
        )
        return [
            PantryFood(id=row.id, name=row.name or "", plural_name=row.plural_name)
            for row in self.session.execute(stmt)
        ]

    def food(self, food_id: UUID4) -> PantryFood | None:
        stmt = select(IngredientFoodModel.id, IngredientFoodModel.name, IngredientFoodModel.plural_name).where(
            IngredientFoodModel.group_id == self.group_id, IngredientFoodModel.id == food_id
        )
        row = self.session.execute(stmt).first()
        return PantryFood(id=row.id, name=row.name or "", plural_name=row.plural_name) if row else None

    def staple_ids(self, household_id: UUID4 | None = None) -> set[UUID4]:
        """The ids of the foods one household has marked; this repository's household by default."""

        link = households_to_ingredient_foods
        stmt = (
            select(link.c.food_id)
            .join(IngredientFoodModel, IngredientFoodModel.id == link.c.food_id)
            .where(
                link.c.household_id == (household_id or self.household_id),
                IngredientFoodModel.group_id == self.group_id,
            )
        )
        return set(self.session.execute(stmt).scalars())

    def staples_by_household(self) -> dict[UUID4, list[str]]:
        """The staple names of every household in the group that has any, keyed by household id."""

        link = households_to_ingredient_foods
        stmt = (
            select(link.c.household_id, IngredientFoodModel.name)
            .join(IngredientFoodModel, IngredientFoodModel.id == link.c.food_id)
            .where(IngredientFoodModel.group_id == self.group_id)
        )
        names: dict[UUID4, list[str]] = {}
        for household_id, name in self.session.execute(stmt):
            names.setdefault(household_id, []).append(name or "")
        return names

    def mark(self, food_ids: Iterable[UUID4], household_id: UUID4 | None = None) -> None:
        """Mark foods on hand for one household. Marking one that already is changes nothing."""

        household_id = household_id or self.household_id
        already = self.staple_ids(household_id)
        new = [food_id for food_id in dict.fromkeys(food_ids) if food_id not in already]
        if not new:
            return

        self.session.execute(
            households_to_ingredient_foods.insert(),
            [{"household_id": household_id, "food_id": food_id} for food_id in new],
        )
        self.session.commit()

    def unmark(self, food_id: UUID4) -> None:
        link = households_to_ingredient_foods
        self.session.execute(delete(link).where(link.c.household_id == self.household_id, link.c.food_id == food_id))
        self.session.commit()
