import { BaseAPI } from "../base/base-clients";
import type { PantryLeftOff, PantryMarkOut, PantryOut } from "~/lib/api/types/household";
import type { SuccessResponse } from "~/lib/api/types/response";

const prefix = "/api";

const routes = {
  pantry: `${prefix}/households/pantry`,
  pantryFood: (foodId: string) => `${prefix}/households/pantry/${foodId}`,
  pantryShoppingList: (listId: string) => `${prefix}/households/pantry/shopping-lists/${listId}`,
};

/** The household's pantry staples: foods it usually has, which recipes leave off a shopping list. */
export class HouseholdPantryAPI extends BaseAPI {
  /** The staples, and unmarked foods that look like ones. */
  async get() {
    return await this.requests.get<PantryOut>(routes.pantry);
  }

  /** Mark a food as a staple. Plain forms of it ("kosher salt" for salt) come back in `alsoMarked`. */
  async mark(foodId: string) {
    return await this.requests.put<PantryMarkOut, object>(routes.pantryFood(foodId), {});
  }

  async unmark(foodId: string) {
    return await this.requests.delete<SuccessResponse>(routes.pantryFood(foodId));
  }

  /** The staples the recipes on a shopping list use, and so the list left off. */
  async leftOff(listId: string) {
    return await this.requests.get<PantryLeftOff>(routes.pantryShoppingList(listId));
  }
}
