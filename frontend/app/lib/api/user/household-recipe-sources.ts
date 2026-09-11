import { BaseCRUDAPI } from "../base/base-clients";
import type { RecipeSourceCreate, RecipeSourceLookupOut, RecipeSourceOut } from "~/lib/api/types/household";

const prefix = "/api";

const routes = {
  recipeSources: `${prefix}/households/recipe-sources`,
  recipeSourcesLookup: `${prefix}/households/recipe-sources/lookup`,
  recipeSourcesId: (id: string | number) => `${prefix}/households/recipe-sources/${id}`,
};

/** The household's recipe source list: which sites it trusts, is wary of, or refuses to import from. */
export class HouseholdRecipeSourcesAPI extends BaseCRUDAPI<RecipeSourceCreate, RecipeSourceOut, RecipeSourceCreate> {
  override baseRoute = routes.recipeSources;
  override itemRoute = routes.recipeSourcesId;

  /**
   * What the household thinks of the site behind `url`. The most specific entry answers, so an
   * entry for a parent domain covers its subdomains; `status` and `source` are null when nothing
   * on the list covers the site.
   */
  async lookup(url: string) {
    return await this.requests.get<RecipeSourceLookupOut>(routes.recipeSourcesLookup, { url });
  }
}
