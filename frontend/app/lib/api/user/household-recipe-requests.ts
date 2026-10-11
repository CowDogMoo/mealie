import { BaseAPI } from "../base/base-clients";
import { route } from "../base/route";
import type { RecipeRequestCreate, RecipeRequestOut, RecipeRequests } from "~/lib/api/types/household";

const prefix = "/api";

const routes = {
  recipeRequests: `${prefix}/households/recipe-requests`,
  recipeRequestsId: (id: string) => `${prefix}/households/recipe-requests/${id}`,
};

/**
 * "Find a new recipe" requests from the Recipes page. Asking only records the request; the cluster
 * runner finds and imports the recipe, and reports back on the same row.
 */
export class HouseholdRecipeRequestsAPI extends BaseAPI {
  /** This household's requests, oldest first, optionally only those in `status`. */
  async getAll(status?: string[]) {
    // `route` would spell an absent filter as `status=undefined`, which the API rightly refuses
    return await this.requests.get<RecipeRequests>(route(routes.recipeRequests, status?.length ? { status } : null));
  }

  async create(payload: RecipeRequestCreate) {
    return await this.requests.post<RecipeRequestOut>(routes.recipeRequests, payload);
  }

  /** Cancel a request that is still waiting, or dismiss one that has finished. */
  async clear(id: string) {
    return await this.requests.delete(routes.recipeRequestsId(id));
  }
}
