from pydantic import UUID4

from mealie.db.models.household.shopping_list import ShoppingList
from mealie.db.models.recipe.api_extras import ShoppingListExtras
from mealie.schema.household.group_shopping_list import ShoppingListOut, ShoppingListUpdate

from .repository_generic import HouseholdRepositoryGeneric


class RepositoryShoppingList(HouseholdRepositoryGeneric[ShoppingListOut, ShoppingList]):
    def update(self, item_id: UUID4, data: ShoppingListUpdate) -> ShoppingListOut:  # type: ignore
        return super().update(item_id, data)

    def set_extras(self, item_id: UUID4, changes: dict[str, str | None]) -> ShoppingListOut:
        """Merge key/value pairs into one list's extras, where a `None` value removes the key.

        Extras are a side table of strings that several integrations share, so this merges rather
        than replaces: writing one key must not disturb another writer's.

        It exists because the whole-object paths cannot express "change one extra safely".
        `update` takes a `ShoppingListUpdate`, whose `list_items` defaults to `[]`, and
        `BaseMixins.update` explicitly assigns empty lists to their relationships -- so an update
        built to carry only extras **deletes every item on the list** through the
        `delete-orphan` cascade. `patch` avoids that by round-tripping the full `ShoppingListOut`,
        but then feeds `recipe_references` and `label_settings` back through the relationship
        initializer for a change that never touched them. Neither risk is worth taking to write a
        string, so this walks the extras rows and nothing else.

        Scoping is the repository's own: `_query_one` applies the group and household filters, so
        a list belonging to another household raises here exactly as it does everywhere else.
        """

        entry = self._query_one(match_value=item_id)

        rows_by_key = {row.key_name: row for row in entry.extras}
        for key, value in changes.items():
            row = rows_by_key.get(key)
            if value is None:
                if row is not None:
                    entry.extras.remove(row)
            elif row is not None:
                row.value = value
            else:
                entry.extras.append(ShoppingListExtras(key, value))

        self.session.commit()
        return self.schema.model_validate(entry)
